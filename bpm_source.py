"""
bpm_source.py — Music-reactive DSP engine
  • Capture WASAPI loopback via PyAudioWPatch (son système propre)
  • Fallback : sounddevice mic pickup
  • Multiband FFT energy (bass / mid / treble) par bloc de 512 samples
  • Envelope follower asymétrique : attack 5ms, decay 200ms
  • Normalisation adaptative (percentile 95 glissant)
  • Beat detection par flux spectral dans la bande basse
  • BPM estimation par librosa (buffer 4s) + titre via Windows Media Session

Expose :
  start() / stop()
  get_bpm()   -> float | None
  get_level() -> float 0-1  ← énergie basse fréquence normalisée (pour brightness)
  get_beat_event() -> (ts, seq)

Dépendances : pip install PyAudioWPatch sounddevice librosa numpy requests pywin32
              pip install winrt-Windows.Media.Control winrt-Windows.Foundation
"""
import threading
import time
import re
import collections
import numpy as np

# ── Config ────────────────────────────────────────────────────────────────────
DEVICE_IDX   = 0         # fallback mic si loopback indisponible
CAPTURE_RATE = 44100
BLOCK_SIZE   = 512       # ~12ms par bloc — latence très faible

# Bandes fréquentielles (Hz)
BASS_LO, BASS_HI   = 60,  100   # kick drum uniquement
MID_LO,  MID_HI    = 250, 2000  # snare, voix
HI_LO,   HI_HI     = 2000, 8000 # cymbales, hi-hat

# Envelope follower (en "blocs" à ~86 blocs/s)
ATTACK_COEF  = 0.95    # fast attack  (~12ms)
DECAY_COEF   = 0.88    # slow decay   (~80ms)

# Normalisation adaptative
NORM_HISTORY = 300     # blocs (~3.5s de mémoire de peak)
NORM_PCT     = 95      # percentile pour le max (évite les outliers)
MIN_SIGNAL   = 5e-7    # plancher — en dessous = silence

# Onset detection pour beat events
ONSET_THRESH    = 1.5   # multiplicateur médiane spectrale
ONSET_MIN_GAP   = 0.18  # s anti-doublon

# BPM estimation
CHUNK_SEC    = 4.0
UPDATE_SEC   = 1.5
BPM_MIN      = 60.0
BPM_MAX      = 160.0
EMA_ALPHA    = 0.10
STALE_SEC    = 15.0

# ── État partagé ──────────────────────────────────────────────────────────────
_lock        = threading.Lock()
_current_bpm = None
_last_bpm_t  = 0.0
_level       = 0.0       # énergie basse normalisée 0-1
_beat_seq    = 0
_last_beat_ts = 0.0
_running     = False
_thread      = None

# ── Visualisation data ────────────────────────────────────────────────────────
_spectrum       = [0.0] * 64      # 64-band spectrum for visualization
_bass_viz       = 0.0              # Bass level for viz
_mid_viz        = 0.0              # Mid level for viz
_treble_viz     = 0.0              # Treble level for viz
_waveform       = [0.0] * 256      # Recent waveform samples
_viz_fft_mag    = None             # Last FFT magnitude for spectrum
_band_hist      = {               # Band history for graphs
    "bass": collections.deque([0.0] * 60, maxlen=60),
    "mid": collections.deque([0.0] * 60, maxlen=60),
    "treble": collections.deque([0.0] * 60, maxlen=60),
}


def get_bpm() -> float | None:
    with _lock:
        if _current_bpm is None:
            return None
        if time.monotonic() - _last_bpm_t > STALE_SEC:
            return None
        return _current_bpm


def get_level() -> float:
    """Énergie basse normalisée 0-1. Spike sur kick/beat, bas entre."""
    with _lock:
        return _level


def get_beat_event() -> tuple:
    with _lock:
        return _last_beat_ts, _beat_seq


# ── Visualization data getters ───────────────────────────────────────────────
def get_viz_spectrum() -> list:
    """Get 64-band spectrum for visualization (0-1 normalized)."""
    with _lock:
        return list(_spectrum)


def get_viz_bands() -> tuple:
    """Get (bass, mid, treble) levels for visualization (0-1 each)."""
    with _lock:
        return _bass_viz, _mid_viz, _treble_viz


def get_viz_waveform() -> list:
    """Get recent waveform samples (-1 to 1)."""
    with _lock:
        return list(_waveform)


def get_viz_band_history() -> dict:
    """Get band history dict with bass/mid/treble lists."""
    with _lock:
        return {
            "bass": list(_band_hist["bass"]),
            "mid": list(_band_hist["mid"]),
            "treble": list(_band_hist["treble"]),
        }


# ── Lookup titre → BPM ───────────────────────────────────────────────────────
# Blacklist des sources audio à ignorer
_BLACKLISTED_SOURCES = {
    "twitch",
    "www.twitch.tv",
    "twitch.tv",
    "electron",  # Twitch app
}

def _get_now_playing():
    try:
        import asyncio
        from winrt.windows.media.control import \
            GlobalSystemMediaTransportControlsSessionManager as SMTC
        async def _fetch():
            mgr   = await SMTC.request_async()
            sess  = mgr.get_current_session()
            if sess is None:
                return None, None

            # Vérifier si la source est blacklistée
            try:
                source_app = sess.source_app_user_model_id.lower()
                # Extraire le nom de domaine pour les apps UWP (format: App!abc)
                if "!" in source_app:
                    source_app = source_app.split("!")[0].lower()

                if any(bad in source_app for bad in _BLACKLISTED_SOURCES):
                    print(f"[BPM] Source blacklistée: {source_app}")
                    return None, None
            except:
                pass

            props = await sess.try_get_media_properties_async()
            return props.artist.strip(), props.title.strip()
        artist, title = asyncio.run(_fetch())
        if not title:
            return None
        if " - " in title:
            parts = [p.strip() for p in title.split(" - ", 1)]
            return parts[0], parts[1]
        if artist:
            return artist, title
    except Exception:
        pass
    return None


_bpm_cache: dict[str, float | None] = {}
_spotify_token = None
_token_expiry  = 0.0

def _spotify_auth():
    global _spotify_token, _token_expiry
    if _spotify_token and time.monotonic() < _token_expiry:
        return _spotify_token
    try:
        import requests, base64
        from spotify_config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
        creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        r = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {creds}"},
            timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            _spotify_token = data["access_token"]
            _token_expiry  = time.monotonic() + data["expires_in"] - 60
            return _spotify_token
    except Exception:
        pass
    return None

def _lookup_bpm(artist, song):
    key = f"{artist.lower()}|{song.lower()}"
    if key in _bpm_cache:
        return _bpm_cache[key]
    bpm = None
    try:
        import requests
        token = _spotify_auth()
        if token:
            h = {"Authorization": f"Bearer {token}"}
            clean = re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', '', song).strip()
            r = requests.get("https://api.spotify.com/v1/search",
                params={"q": f"{artist} {clean}", "type": "track", "limit": 1},
                headers=h, timeout=5)
            items = r.json().get("tracks", {}).get("items", [])
            if items:
                r2 = requests.get(
                    f"https://api.spotify.com/v1/audio-features/{items[0]['id']}",
                    headers=h, timeout=5)
                tempo = r2.json().get("tempo")
                if tempo:
                    bpm = float(tempo)
                    while bpm > 160: bpm /= 2
                    while bpm < 60:  bpm *= 2
    except Exception:
        pass
    _bpm_cache[key] = bpm
    return bpm


# ── DSP helpers ───────────────────────────────────────────────────────────────
def _band_energy(fft_mag, sr, n_fft, lo, hi):
    """Énergie dans une bande [lo, hi] Hz."""
    freqs  = np.fft.rfftfreq(n_fft, 1.0 / sr)
    mask   = (freqs >= lo) & (freqs <= hi)
    return float(np.sum(fft_mag[mask] ** 2))


def _audio_bpm(ring, buf_size):
    if len(ring) < buf_size:
        return None
    try:
        import librosa
        audio   = np.array(ring, dtype=np.float32)
        if np.sqrt(np.mean(audio ** 2)) < MIN_SIGNAL:
            return None
        audio_r = librosa.resample(audio, orig_sr=CAPTURE_RATE, target_sr=22050)
        onset   = librosa.onset.onset_strength(y=audio_r, sr=22050)
        ests    = []
        try:
            t1, _ = librosa.beat.beat_track(onset_envelope=onset, sr=22050)
            ests.append(float(np.atleast_1d(t1)[0]))
        except Exception: pass
        try:
            t2 = librosa.feature.tempo(onset_envelope=onset, sr=22050)
            ests.append(float(np.atleast_1d(t2)[0]))
        except Exception: pass
        try:
            pulse = librosa.beat.plp(onset_envelope=onset, sr=22050)
            t3 = librosa.feature.tempo(onset_envelope=pulse, sr=22050)
            ests.append(float(np.atleast_1d(t3)[0]))
        except Exception: pass
        if not ests:
            return None
        def _norm(b):
            while b < BPM_MIN: b *= 2
            while b > BPM_MAX: b /= 2
            return b
        ests = [_norm(e) for e in ests]
        med  = float(np.median(ests))
        close = [e for e in ests if abs(e - med) / med < 0.08]
        return float(np.mean(close)) if close else med
    except Exception:
        return None


# ── Capture audio (PyAudioWPatch loopback ou sounddevice mic) ─────────────────
def _open_stream(queue_out, running_flag):
    """
    Essaie PyAudioWPatch (WASAPI loopback), sinon sounddevice mic.
    Pousse des blocs numpy float32 mono dans queue_out.
    """
    try:
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        wasapi_idx = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]

        # Collecte tous les loopback devices
        loopbacks = []
        for i in range(pa.get_host_api_info_by_index(wasapi_idx)["deviceCount"]):
            dev = pa.get_device_info_by_host_api_device_index(wasapi_idx, i)
            if dev.get("isLoopbackDevice", False) and dev["maxInputChannels"] > 0:
                loopbacks.append(dev)
                print(f"[BPM] loopback trouvé: {dev['name']}")

        if not loopbacks:
            raise RuntimeError("Aucun loopback trouvé")

        # Priorité dans l'ordre : Speakers > HD Audio > Main 1/2 > premier dispo
        PREFER = ["speakers", "hd audio", "main 1/2"]
        loopback_dev = loopbacks[0]
        for pref in PREFER:
            match = next((d for d in loopbacks if pref in d["name"].lower()), None)
            if match:
                loopback_dev = match
                break

        rate   = int(loopback_dev["defaultSampleRate"])
        ch     = loopback_dev["maxInputChannels"]
        dev_idx = loopback_dev["index"]
        print(f"[BPM] WASAPI loopback: {loopback_dev['name']} @ {rate}Hz")

        def _paw_cb(in_data, frame_count, time_info, status):
            if not running_flag[0]:
                return (None, pyaudio.paComplete)
            raw = np.frombuffer(in_data, dtype=np.float32)
            if ch > 1:
                raw = raw.reshape(-1, ch).mean(axis=1)
            # Resample vers CAPTURE_RATE si besoin
            if rate != CAPTURE_RATE:
                import librosa
                raw = librosa.resample(raw, orig_sr=rate, target_sr=CAPTURE_RATE)
            queue_out.append(raw)
            return (None, pyaudio.paContinue)

        stream = pa.open(
            format=pyaudio.paFloat32,
            channels=ch,
            rate=rate,
            input=True,
            input_device_index=dev_idx,
            frames_per_buffer=BLOCK_SIZE,
            stream_callback=_paw_cb,
        )
        stream.start_stream()
        print("[BPM] DSP engine actif (loopback propre)")
        while running_flag[0] and stream.is_active():
            time.sleep(0.05)
        stream.stop_stream()
        stream.close()
        pa.terminate()
        return

    except Exception as e:
        print(f"[BPM] PyAudioWPatch indisponible ({e}), fallback mic")

    # Fallback sounddevice
    try:
        import sounddevice as sd
        n_ch = min(2, sd.query_devices(DEVICE_IDX)["max_input_channels"])
        print(f"[BPM] device [{DEVICE_IDX}]: {sd.query_devices(DEVICE_IDX)['name']}")

        def _sd_cb(indata, frames, t, status):
            mono = indata.mean(axis=1) if indata.ndim > 1 else indata[:, 0]
            queue_out.append(mono.astype(np.float32))

        with sd.InputStream(device=DEVICE_IDX, channels=n_ch,
                            samplerate=CAPTURE_RATE, blocksize=BLOCK_SIZE,
                            dtype="float32", callback=_sd_cb):
            print("[BPM] DSP engine actif (mic fallback)")
            while running_flag[0]:
                time.sleep(0.05)
    except Exception as e:
        print(f"[BPM] erreur audio: {e}")


# ── Boucle principale ─────────────────────────────────────────────────────────
def _detector_loop():
    global _current_bpm, _last_bpm_t, _level, _beat_seq, _last_beat_ts, _running

    # Buffers DSP
    env_bass   = 0.0
    peak_hist  = collections.deque(maxlen=NORM_HISTORY)
    prev_mag   = None
    flux_buf   = collections.deque(maxlen=40)
    last_onset = 0.0

    # Buffer librosa (BPM)
    buf_size   = int(CHUNK_SEC * CAPTURE_RATE)
    ring       = collections.deque(maxlen=buf_size)

    # Queue audio inter-thread
    audio_queue = collections.deque(maxlen=200)
    running_flag = [True]

    # Titre / BPM
    ema_bpm    = None
    mb_bpm     = None
    last_title = ""
    _title_result = [None]

    def _title_worker():
        while _running:
            _title_result[0] = _get_now_playing()
            time.sleep(5.0)
    threading.Thread(target=_title_worker, daemon=True).start()

    # Démarre la capture dans un thread séparé
    cap_thread = threading.Thread(
        target=_open_stream, args=(audio_queue, running_flag), daemon=True
    )
    cap_thread.start()

    # Boucle DSP — consomme la queue audio
    def _process_block(mono):
        nonlocal env_bass, prev_mag, last_onset
        global _level, _beat_seq, _last_beat_ts, _viz_fft_mag

        ring.extend(mono)

        n    = len(mono)
        if n < 2:
            return
        win  = mono * np.hanning(n)
        mag  = np.abs(np.fft.rfft(win))

        # Store for visualization
        _viz_fft_mag = mag.copy() if _viz_fft_mag is None else _viz_fft_mag * 0.7 + mag * 0.3

        e_bass = _band_energy(mag, CAPTURE_RATE, n, BASS_LO, BASS_HI)
        e_mid  = _band_energy(mag, CAPTURE_RATE, n, MID_LO, MID_HI)
        e_treble = _band_energy(mag, CAPTURE_RATE, n, HI_LO, HI_HI)
        e_bass = max(e_bass, 0.0) ** 0.5

        if e_bass > env_bass:
            env_bass = ATTACK_COEF * env_bass + (1 - ATTACK_COEF) * e_bass
        else:
            env_bass = DECAY_COEF  * env_bass

        peak_hist.append(env_bass)
        if len(peak_hist) >= 10:
            peak = float(np.percentile(peak_hist, NORM_PCT))
            norm = env_bass / max(peak, MIN_SIGNAL)
        else:
            norm = 0.0

        # Update visualization data
        with _lock:
            _level = min(1.0, norm)

            # Update spectrum (64 bands, log scale)
            if len(mag) >= 64:
                # Logarithmic binning
                bins = []
                for i in range(64):
                    # Log scale from index 2 to len(mag)-1
                    lo_idx = int(2 + (len(mag) - 3) ** (i / 63))
                    hi_idx = int(2 + (len(mag) - 3) ** ((i + 1) / 63))
                    if hi_idx > len(mag):
                        hi_idx = len(mag)
                    if lo_idx < hi_idx:
                        bin_val = np.mean(mag[lo_idx:hi_idx])
                        bins.append(min(1.0, bin_val * 20))
                    else:
                        bins.append(0.0)
                _spectrum[:] = bins

            # Update band levels (normalized for viz)
            _bass_viz = min(1.0, e_bass * 100)
            _mid_viz = min(1.0, e_mid * 80)
            _treble_viz = min(1.0, e_treble * 60)

            # Update band history
            _band_hist["bass"].append(_bass_viz)
            _band_hist["mid"].append(_mid_viz)
            _band_hist["treble"].append(_treble_viz)

            # Update waveform (center 256 samples)
            wave_start = max(0, len(mono) // 2 - 128)
            wave_end = min(len(mono), wave_start + 256)
            wave_samples = mono[wave_start:wave_end]
            if len(wave_samples) < 256:
                wave_samples = np.concatenate([wave_samples, np.zeros(256 - len(wave_samples))])
            _waveform[:] = wave_samples[:256]

        if prev_mag is not None and len(mag) == len(prev_mag):
            # Flux spectral sur la bande kick uniquement (60-100 Hz)
            freqs     = np.fft.rfftfreq(n * 2 - 2, 1.0 / CAPTURE_RATE)
            kick_mask = (freqs >= 60) & (freqs <= 100)
            diff      = mag - prev_mag
            diff_kick = diff[kick_mask[:len(diff)]]
            flux      = float(np.sum(diff_kick[diff_kick > 0]))
            flux_buf.append(flux)
            if len(flux_buf) >= 8:
                thresh = float(np.median(flux_buf)) * ONSET_THRESH
                now    = time.monotonic()
                if flux > thresh and (now - last_onset) >= ONSET_MIN_GAP:
                    last_onset = now
                    with _lock:
                        _beat_seq    += 1
                        _last_beat_ts = now
        prev_mag = mag  # noqa: F841 — fermeture

    last_bpm_update = 0.0
    try:
        while _running:
            # Consomme tous les blocs disponibles
            while audio_queue:
                _process_block(audio_queue.popleft())
            time.sleep(0.005)

            now = time.monotonic()
            if now - last_bpm_update < UPDATE_SEC:
                continue
            last_bpm_update = now

            # Titre → lookup BPM
            now_playing = _title_result[0]
            if now_playing:
                title = f"{now_playing[0]}|{now_playing[1]}"
                if title != last_title:
                    last_title = title
                    artist, song = now_playing
                    print(f"[BPM] ~ {artist} - {song}")
                    ema_bpm = None
                    mb_bpm  = None
                    result = _lookup_bpm(artist, song)
                    if result:
                        print(f"[BPM] Spotify → {result:.0f} BPM")
                        mb_bpm = ema_bpm = result

            if mb_bpm:
                with _lock:
                    _current_bpm = mb_bpm
                    _last_bpm_t  = now
                continue

            raw = _audio_bpm(ring, buf_size)
            if raw is None:
                continue
            ema_bpm = raw if ema_bpm is None else ema_bpm * (1-EMA_ALPHA) + raw * EMA_ALPHA
            with _lock:
                _current_bpm = ema_bpm
                _last_bpm_t  = now

    except Exception as e:
        print(f"[BPM] erreur: {e}")
        import traceback; traceback.print_exc()
    finally:
        running_flag[0] = False
        _running = False


def start():
    global _running, _thread
    if _running:
        return
    _running = True
    _thread  = threading.Thread(target=_detector_loop, daemon=True)
    _thread.start()


def stop():
    global _running
    _running = False


# ── Test standalone ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("DSP music engine — lance de la musique. Ctrl+C pour stop.\n")
    start()
    try:
        while True:
            time.sleep(0.05)
            lv  = get_level()
            bpm = get_bpm()
            bar = "█" * int(lv * 40) + "░" * (40 - int(lv * 40))
            bpm_str = f"{bpm:.0f} BPM" if bpm else "--- BPM"
            print(f"\r  [{bar}] {lv:.2f}  {bpm_str}    ", end="", flush=True)
    except KeyboardInterrupt:
        stop()
