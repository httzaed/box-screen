"""
audio_viz_source.py — Real-time audio visualization data (SAFE VERSION)
  Captures audio and provides data for 4 visualization types:
    1. Spectrum (FFT frequency bins)
    2. Waveform (time-domain samples)
    3. Beat indicator (level + beat detection)
    4. Frequency bands (bass/mid/treble energy)

This version uses polling instead of callbacks to avoid segfaults.

Usage:
    start()
    spectrum = get_spectrum()      # List of frequency bin values
    waveform = get_waveform()      # List of recent samples
    level, beat = get_level_beat()  # Current level and beat status
    bands = get_bands()            # (bass, mid, treble) energies
    stop()
"""

import threading
import time
import collections
import array
import numpy as np

# ── Config ───────────────────────────────────────────────────────────────────
DEVICE_IDX   = 0
CAPTURE_RATE = 44100
BLOCK_SIZE   = 1024        # Larger block for better frequency resolution
FFT_SIZE     = 2048

# Frequency bands (Hz)
BASS_LO, BASS_HI   = 20,  250
MID_LO,  MID_HI    = 250, 2000
TREBLE_LO, TREBLE_HI = 2000, 16000

# Spectrum config
NUM_BINS     = 64          # Number of spectrum bars
LOG_SCALE    = True        # Use logarithmic frequency scale

# Waveform config
WAVEFORM_LEN = 256         # Number of samples to display

# Envelope follower
ATTACK_COEF  = 0.90
DECAY_COEF   = 0.85

# ── State ─────────────────────────────────────────────────────────────────────
_lock        = threading.Lock()
_running     = False
_thread      = None

# Visualization data buffers
_spectrum    = [0.0] * NUM_BINS
_waveform    = [0.0] * WAVEFORM_LEN
_level       = 0.0
_beat_active = False
_beat_seq    = 0
_last_beat_t = 0.0

# Frequency bands
_bass_level   = 0.0
_mid_level    = 0.0
_treble_level = 0.0

# Band history for graphs
_bass_hist   = collections.deque([0.0] * 100, maxlen=100)
_mid_hist    = collections.deque([0.0] * 100, maxlen=100)
_treble_hist = collections.deque([0.0] * 100, maxlen=100)

# DSP state
_env_bass    = 0.0
_prev_fft    = None
_flux_buf    = collections.deque(maxlen=30)
_last_onset  = 0.0


def _freq_to_bin(freq, fft_size, sample_rate):
    """Convert frequency to FFT bin index."""
    return int(freq * fft_size / sample_rate)


def _make_spectrum_bins(fft_size, sample_rate, num_bins, log_scale):
    """Generate frequency bin mappings for spectrum display."""
    if log_scale:
        # Logarithmic scale from ~20Hz to Nyquist
        min_freq = 20
        max_freq = sample_rate / 2
        bins = []
        for i in range(num_bins):
            f = min_freq * (max_freq / min_freq) ** (i / (num_bins - 1))
            bin_idx = _freq_to_bin(f, fft_size, sample_rate)
            bins.append(bin_idx)
        return bins
    else:
        # Linear scale
        max_bin = fft_size // 2
        step = max_bin // num_bins
        return [i * step for i in range(num_bins)]


# Pre-compute bin mappings
_SPECTRUM_BINS = _make_spectrum_bins(FFT_SIZE, CAPTURE_RATE, NUM_BINS, LOG_SCALE)
_BIN_BASS_LO   = _freq_to_bin(BASS_LO, FFT_SIZE, CAPTURE_RATE)
_BIN_BASS_HI   = _freq_to_bin(BASS_HI, FFT_SIZE, CAPTURE_RATE)
_BIN_MID_LO    = _freq_to_bin(MID_LO, FFT_SIZE, CAPTURE_RATE)
_BIN_MID_HI    = _freq_to_bin(MID_HI, FFT_SIZE, CAPTURE_RATE)
_BIN_TREBLE_LO = _freq_to_bin(TREBLE_LO, FFT_SIZE, CAPTURE_RATE)
_BIN_TREBLE_HI = _freq_to_bin(TREBLE_HI, FFT_SIZE, CAPTURE_RATE)


def get_spectrum():
    """Get current spectrum values (list of NUM_BINS floats, 0-1)."""
    with _lock:
        return list(_spectrum)


def get_waveform():
    """Get current waveform samples (list of WAVEFORM_LEN floats, -1 to 1)."""
    with _lock:
        return list(_waveform)


def get_level_beat():
    """Get (level, beat_active, beat_seq)."""
    with _lock:
        return _level, _beat_active, _beat_seq


def get_bands():
    """Get (bass, mid, treble) levels (0-1 each)."""
    with _lock:
        return _bass_level, _mid_level, _treble_level


def get_band_history():
    """Get (bass_hist, mid_hist, treble_hist) as lists."""
    with _lock:
        return list(_bass_hist), list(_mid_hist), list(_treble_hist)


# ── Audio capture using sounddevice (more stable) ─────────────────────────────
def _viz_loop_sounddevice():
    """Main visualization loop using sounddevice with polling."""
    global _running

    try:
        import sounddevice as sd
    except ImportError:
        print("[AUDIO VIZ] sounddevice not installed - pip install sounddevice")
        return

    print("[AUDIO VIZ] Using sounddevice capture")

    # Use default input device
    try:
        device_info = sd.query_devices(DEVICE_IDX)
        print(f"[AUDIO VIZ] Device: {device_info['name']}")
    except:
        print("[AUDIO VIZ] Using default device")

    try:
        # Create a buffer for audio data
        audio_buffer = collections.deque(maxlen=10)

        def _audio_callback(indata, frames, time, status):
            """Called by sounddevice for each audio block."""
            if status:
                print(f"[AUDIO VIZ] Status: {status}")
            # Convert to mono and store
            if indata.ndim > 1:
                mono = indata.mean(axis=1)
            else:
                mono = indata[:, 0]
            audio_buffer.append(mono.copy())

        # Start stream
        with sd.InputStream(device=DEVICE_IDX, channels=1,
                            samplerate=CAPTURE_RATE, blocksize=BLOCK_SIZE,
                            dtype="float32", callback=_audio_callback):
            print("[AUDIO VIZ] Started")

            while _running:
                # Process available audio blocks
                while audio_buffer:
                    block = audio_buffer.popleft()
                    _process_block(block)

                time.sleep(0.01)

    except Exception as e:
        print(f"[AUDIO VIZ] Error: {e}")
        import traceback
        traceback.print_exc()


# ── Processing loop ───────────────────────────────────────────────────────────
def _process_block(mono):
    """Process audio block and update visualization buffers."""
    global _env_bass, _prev_fft, _last_onset

    # Convert to numpy if needed
    if not isinstance(mono, np.ndarray):
        mono = np.array(mono, dtype=np.float32)

    n = len(mono)
    if n < FFT_SIZE:
        mono = np.concatenate([mono, np.zeros(FFT_SIZE - n)])
    elif n > FFT_SIZE:
        mono = mono[:FFT_SIZE]

    # Window and FFT
    win = mono * np.hanning(FFT_SIZE)
    fft_mag = np.abs(np.fft.rfft(win))

    # Normalize FFT
    fft_norm = fft_mag / FFT_SIZE

    # Update spectrum bins
    new_spectrum = []
    for i in range(len(_SPECTRUM_BINS) - 1):
        lo = _SPECTRUM_BINS[i]
        hi = _SPECTRUM_BINS[i + 1]
        if hi > len(fft_norm):
            hi = len(fft_norm)
        if lo < hi:
            bin_val = np.mean(fft_norm[lo:hi])
            # Log scale for better visualization
            new_spectrum.append(min(1.0, bin_val * 10))
        else:
            new_spectrum.append(0.0)
    new_spectrum.append(min(1.0, np.mean(fft_norm[_SPECTRUM_BINS[-1]:]) * 10))

    # Apply smoothing
    for i in range(len(new_spectrum)):
        new_spectrum[i] = 0.7 * _spectrum[i] + 0.3 * new_spectrum[i]

    # Update waveform (center of block)
    wave_start = (FFT_SIZE - WAVEFORM_LEN) // 2
    new_waveform = mono[wave_start:wave_start + WAVEFORM_LEN].tolist()

    # Calculate band energies
    bass_energy = np.sum(fft_norm[_BIN_BASS_LO:_BIN_BASS_HI] ** 2) ** 0.5
    mid_energy = np.sum(fft_norm[_BIN_MID_LO:_BIN_MID_HI] ** 2) ** 0.5
    treble_energy = np.sum(fft_norm[_BIN_TREBLE_LO:_BIN_TREBLE_HI] ** 2) ** 0.5

    # Envelope follower for bass (beat detection)
    if bass_energy > _env_bass:
        _env_bass = ATTACK_COEF * _env_bass + (1 - ATTACK_COEF) * bass_energy
    else:
        _env_bass = DECAY_COEF * _env_bass

    # Normalize levels
    bass_norm = min(1.0, _env_bass * 50)
    mid_norm = min(1.0, mid_energy * 30)
    treble_norm = min(1.0, treble_energy * 25)

    # Beat detection via spectral flux
    onset = False
    if _prev_fft is not None and len(fft_mag) == len(_prev_fft):
        # Flux in bass band only
        diff = fft_mag - _prev_fft
        bass_diff = diff[_BIN_BASS_LO:_BIN_BASS_HI]
        flux = float(np.sum(bass_diff[bass_diff > 0]))
        _flux_buf.append(flux)

        if len(_flux_buf) >= 8:
            thresh = float(np.median(_flux_buf)) * 1.8
            now = time.monotonic()
            if flux > thresh and (now - _last_onset) >= 0.15:
                _last_onset = now
                onset = True

    _prev_fft = fft_mag.copy()

    # Update global state
    with _lock:
        _spectrum[:] = new_spectrum
        _waveform[:] = new_waveform
        _level = bass_norm
        _bass_level = bass_norm
        _mid_level = mid_norm
        _treble_level = treble_norm

        _bass_hist.append(bass_norm)
        _mid_hist.append(mid_norm)
        _treble_hist.append(treble_norm)

        if onset:
            _beat_seq += 1
            _last_beat_t = time.monotonic()
            _beat_active = True
        else:
            # Clear beat after short duration
            if time.monotonic() - _last_beat_t > 0.1:
                _beat_active = False


def start():
    """Start audio visualization capture."""
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_viz_loop_sounddevice, daemon=True)
    _thread.start()


def stop():
    """Stop audio visualization capture."""
    global _running
    _running = False


# ── Test ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Audio Viz Source — press Ctrl+C to stop\n")
    start()
    try:
        while True:
            time.sleep(0.05)
            lvl, beat, seq = get_level_beat()
            bars = "█" * int(lvl * 40) + "░" * (40 - int(lvl * 40))
            b, m, t = get_bands()
            print(f"\r  [{bars}] L:{lvl:.2f} B:{b:.2f} M:{m:.2f} T:{t:.2f} Beat:{beat} #{seq}    ",
                  end="", flush=True)
    except KeyboardInterrupt:
        stop()
        print("\nStopped")
