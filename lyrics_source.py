"""
lyrics_source.py - Paroles en temps réel via lrclib.net
  • Détecte la chanson via Windows Media Session (même méthode que bpm_source)
  • Fetch les paroles synchronisées (LRC) depuis lrclib.net (gratuit, sans clé)
  • Lit la position de lecture via WinRT pour suivre la ligne courante
  • Expose get_state() -> artist, title, lines, current_idx

Expose :
  start() / stop()
  get_state() -> dict | None
"""
import threading
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── État partagé ──────────────────────────────────────────────────────────────
_lock    = threading.Lock()
_state   = None          # dict: artist, title, lines, current_idx, synced
_running = False
_thread  = None

# ── Beat tracking pour sync audio ──────────────────────────────────────────────
_beat_counter     = 0       # nombre de beats depuis début chanson
_last_beat_ts     = None    # timestamp du dernier beat (time.monotonic)
_last_beat_seq    = None    # séquence BPM source
_song_start_beats = None    # beat_seq quand la chanson a commencé
_est_bpm          = None    # BPM estimé depuis les beats
_last_sync_pos    = None    # dernière position connue (secondes) resyncée sur beat

# ── Auto-calibration offset (Kalman filter) ────────────────────────────────────────
_calibration_active    = False  # en cours de calibration?
_calibration_samples   = []     # historique des samples (timestamp, offset)
_calibrated_offset    = None   # offset calibré pour cette chanson
_calibration_done      = False  # calibration terminée pour cette chanson?
_first_listen_mode     = False  # True si c'est la première écoute jamais de cette chanson
_next_sample_pos       = 5.0    # prochaine position (secondes) où on prend un sample
_sample_interval       = 5.0    # intervalle entre samples (5s pour bonne précision)

# ── Kalman filter state ────────────────────────────────────────────────────────────
_kf_state      = None    # état estimé (offset)
_kf_covariance = None    # covariance de l'estimation
_kf_process_noise   = 0.05  # bruit du processus (stabilité de l'offset)
_kf_measurement_noise = 0.2   # bruit de mesure (imprécision des timestamps)

# ── Calibration statistiques ────────────────────────────────────────────────────────
_calibration_stats = {       # stats sur les samples
    "mean": None,
    "std": None,
    "outliers_removed": 0,
    "confidence": 0.0,
}

# ── Annulation de recherche ─────────────────────────────────────────────────────────
_current_search_key = None    # clé de la recherche en cours (pour annulation)

# ── YouTube extension sync ─────────────────────────────────────────────────────
_youtube_pos      = None    # position depuis extension (secondes)
_youtube_pos_t    = None    # timestamp monotonic quand reçu
_youtube_artist   = None
_youtube_title    = None
_youtube_video_id = None
_youtube_captions_tried = False  # se souvenir si on a déjà essayé les captions pour cette vidéo
_youtube_last_video_id = None  # dernier video_id confirmé comme actif
_youtube_video_ids_seen = {}  # timestamp de dernière vue de chaque video_id


def set_youtube_position(video_id: str, artist: str, title: str, position: float):
    """
    Appelé par le control_server quand l'extension Chrome envoie des données.
    Filtre les vidéos de playlist pour ne garder que la vraie vidéo active.
    """
    global _youtube_pos, _youtube_pos_t, _youtube_artist, _youtube_title, _youtube_video_id
    global _youtube_last_video_id, _youtube_video_ids_seen, _youtube_captions_tried

    now = time.monotonic()
    _youtube_video_ids_seen[video_id] = now

    # Détection de la vraie vidéo active :
    # - Position > 1.0s (évite les buffer/reset)
    # - Même video_id que précédemment (stabilité)
    # - OU nouvelle vidéo avec position avancée

    current_pos = float(position) if position is not None else None

    # Si la position est trop basse ou None, ignorer (playlist buffer)
    if current_pos is None or current_pos < 1.0:
        return  # Ignore les vidéos de playlist en buffer

    # Si on a déjà une vidéo active et que c'est différente, vérifier si c'est vraiment un changement
    if _youtube_last_video_id and _youtube_last_video_id != video_id:
        # Vérifier si l'ancienne vidéo est encore "vivante" (vue récemment)
        last_seen = _youtube_video_ids_seen.get(_youtube_last_video_id, 0)
        if now - last_seen < 2.0:  # L'ancienne est encore envoyée = playlist
            # Garder l'ancienne si elle continue d'être envoyée
            return

    # Nouvelle vidéo active confirmée
    with _lock:
        _youtube_pos = current_pos
        _youtube_pos_t = now
        _youtube_artist = artist
        _youtube_title = title
        _youtube_video_id = video_id
        _youtube_last_video_id = video_id

# ── Kalman filter pour calibration offset ────────────────────────────────────────
def _kalman_init():
    """Initialise le Kalman filter pour une nouvelle calibration."""
    global _kf_state, _kf_covariance, _calibration_stats
    _kf_state = 0.0  # offset initial estimé
    _kf_covariance = 1.0  # incertitude initiale élevée
    _calibration_stats = {
        "mean": None,
        "std": None,
        "outliers_removed": 0,
        "confidence": 0.0,
    }

def _kalman_update(measurement: float) -> float:
    """
    Met à jour le Kalman filter avec une nouvelle mesure d'offset.
    Retourne l'estimation mise à jour.
    """
    global _kf_state, _kf_covariance

    if _kf_state is None:
        _kalman_init()

    # 1) Prédiction (l'offset est stable)
    # x_predicted = x_previous (offset ne change pas)
    # P_predicted = P_previous + Q (incertitude augmente)
    _kf_covariance += _kf_process_noise

    # 2) Gain de Kalman
    # K = P / (P + R)
    kalman_gain = _kf_covariance / (_kf_covariance + _kf_measurement_noise)

    # 3) Mise à jour de l'état
    # x = x + K * (measurement - x)
    _kf_state = _kf_state + kalman_gain * (measurement - _kf_state)

    # 4) Mise à jour de la covariance
    # P = (1 - K) * P
    _kf_covariance = (1 - kalman_gain) * _kf_covariance

    return _kf_state

def _remove_outliers_iqr(samples: list) -> list:
    """
    Élimine les outliers en utilisant l'IQR (Interquartile Range).
    Retourne les samples filtrés.
    """
    if len(samples) < 5:
        return samples  # pas assez de données

    offsets = [s[1] for s in samples]  # (timestamp, offset)
    q1 = sorted(offsets)[len(offsets) // 4]
    q3 = sorted(offsets)[3 * len(offsets) // 4]
    iqr = q3 - q1

    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr

    filtered = [(ts, off) for ts, off in samples if lower_bound <= off <= upper_bound]
    removed = len(samples) - len(filtered)

    global _calibration_stats
    _calibration_stats["outliers_removed"] += removed

    if removed > 0:
        print(f"[LYRICS] 🧹 {removed} outlier(s) éliminé(s) (IQR: [{lower_bound:.2f}, {upper_bound:.2f}])")

    return filtered

def _update_calibration_stats(samples: list):
    """Met à jour les statistiques de calibration."""
    if not samples:
        return

    offsets = [s[1] for s in samples]

    # Moyenne et écart-type
    import statistics as _stats
    try:
        mean = _stats.mean(offsets)
        std = _stats.stdev(offsets) if len(offsets) > 1 else 0.0
    except Exception:
        mean = sum(offsets) / len(offsets)
        std = 0.0

    # Confiance : inverse de l'écart-type (normalisé 0-1)
    confidence = max(0.0, min(1.0, 1.0 - std / 2.0))

    _calibration_stats["mean"] = mean
    _calibration_stats["std"] = std
    _calibration_stats["confidence"] = confidence

def _get_weighted_average(samples: list) -> float:
    """
    Calcule une moyenne pondérée avec exponentielle décroissante.
    Samples récents = plus de poids.
    """
    if not samples:
        return 0.0

    # Poids exponentiel : w = exp(-lambda * age)
    # lambda ajusté pour que les vieux aient moins de poids
    import math
    now_ts = time.monotonic()

    total_weight = 0.0
    weighted_sum = 0.0

    for ts, offset in samples:
        age = now_ts - ts
        weight = math.exp(-0.1 * age)  # décroissance lente
        weighted_sum += offset * weight
        total_weight += weight

    return weighted_sum / total_weight if total_weight > 0 else 0.0

# ── WinRT helpers ─────────────────────────────────────────────────────────────

def _winrt_fetch() -> tuple | None:
    """
    Appelle WinRT depuis un thread frais (requis pour l'init COM sur Windows).
    Retourne (artist, title, position_s) ou None.
    """
    result = [None]
    error = [None]

    def _worker():
        try:
            import asyncio
            from winrt.windows.media.control import \
                GlobalSystemMediaTransportControlsSessionManager as SMTC

            async def _fetch():
                mgr  = await SMTC.request_async()
                if mgr is None:
                    return None
                sess = mgr.get_current_session()
                if sess is None:
                    return None
                props = await sess.try_get_media_properties_async()
                if props is None:
                    return None
                artist = (props.artist or "").strip()
                title  = (props.title  or "").strip()
                if not title:
                    return None
                # Position de lecture
                try:
                    tl  = sess.get_timeline_properties()
                    pos = tl.position
                    pos_s = pos.total_seconds() if hasattr(pos, "total_seconds") else pos.duration / 1e7
                except Exception:
                    pos_s = None
                return artist, title, pos_s

            result[0] = asyncio.run(_fetch())
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=4.0)  # 4 secondes
    if t.is_alive():
        print("[LYRICS] WARNING: WinRT timeout - thread still running")
        return None
    if error[0]:
        print(f"[LYRICS] WinRT error: {type(error[0]).__name__}: {error[0]}")
        return None
    return result[0]


def _get_now_playing():
    """Retourne (artist, title) ou None."""
    r = _winrt_fetch()
    if r is None:
        return None
    artist, title, _ = r
    if not title:
        return None
    # Nettoie les noms (retire " - Topic - " etc.)
    if artist:
        artist = _remove_topic_noise(artist)
    if " - " in title and not artist:
        parts = [p.strip() for p in title.split(" - ", 1)]
        return parts[0], parts[1]
    return (artist or ""), title


def _get_playback_position() -> float | None:
    """Retourne la position de lecture en secondes, ou None."""
    r = _winrt_fetch()
    return r[2] if r else None


# ── LRC parser ────────────────────────────────────────────────────────────────

_LRC_RE = re.compile(r"^\[(\d{1,3}):(\d{2})\.(\d{1,3})\](.*)")

def _parse_lrc(lrc_text: str) -> list[tuple[float, str]]:
    """Retourne [(seconds, line_text), ...] trié par temps."""
    lines = []
    for raw in lrc_text.splitlines():
        m = _LRC_RE.match(raw.strip())
        if not m:
            continue
        minutes   = int(m.group(1))
        seconds   = int(m.group(2))
        centis_s  = m.group(3)
        # normalise en fractions de seconde (centièmes ou millièmes)
        frac      = int(centis_s) / (10 ** len(centis_s))
        ts        = minutes * 60 + seconds + frac
        text      = m.group(4).strip()
        lines.append((ts, text))
    lines.sort(key=lambda x: x[0])
    return lines


# ── Nettoyage artiste / titre ─────────────────────────────────────────────────

# Suffixes YouTube Music / streaming à ignorer
# Match : "ArtistVEVO", "Artist - VEVO", "Artist - Topic", etc.
_ARTIST_NOISE = re.compile(
    r'[-–]?\s*(Topic|Official|VEVO|Music|Records?|Channel|Artist)\s*$',
    re.IGNORECASE
)

def _remove_topic_noise(text: str) -> str:
    """Retire ' - Topic - ' et autres bruits YouTube des noms."""
    if not text:
        return text
    import re as _re
    # Retire " - Topic - " (avec espaces autour)
    text = text.replace(" - Topic - ", " - ")
    # Retire aussi " - Topic" ou "Topic -" (en fin de chaîne)
    text = text.replace(" - Topic", "").replace("Topic - ", "")
    # Retire les parenthèses avec pseudos (playlist channels)
    text = _re.sub(r'\s*\([^)]*mimi[^)]*\)', '', text, flags=_re.IGNORECASE).strip()
    text = _re.sub(r'\s*\([^)]*dj[^)]*\)', '', text, flags=_re.IGNORECASE).strip()
    # Nettoie les espaces doubles
    return " ".join(text.split())

def _clean_artist(artist: str) -> str:
    """Retire les suffixes parasites type '- Topic', '- VEVO', '| Vevo', etc."""
    a = " ".join(artist.split())

    # ── Nettoyage des noms de chaînes YouTube (playlists, remixers, etc.) ─────────────
    # Retire les parenthèses avec pseudos/infos: "hedjient (mimijil)" -> "hedjient"
    a = re.sub(r'\s*\([^)]*mimijil[^)]*\)', '', a, flags=re.IGNORECASE).strip()
    a = re.sub(r'\s*\([^)]*\)', '', a).strip()  # Toutes les parenthèses

    # Retire les marques de playlist/channel: "| Channel", " - Channel", etc.
    a = re.sub(r'\s*\|\s*[\w\s]+$', '', a).strip()
    a = re.sub(r'\s*[-–]\s*Channel\s*$', '', a, flags=re.IGNORECASE).strip()

    # ── Exceptions spéciales ───────────────────────────────────────────────────────────
    # Tyler, The Creator : corrige les fausses détections YouTube/SMTC
    if "tyler" in a.lower() and "creator" in a.lower():
        a = "Tyler, The Creator"
    elif "the creator" in a.lower() and "boot syins" in a.lower():
        a = "Tyler, The Creator"  # Cas: "After The Storm , The Creator, Bootsy Collins ft. tyler"

    # Cas particulier: "georgemichaelVEVO" -> "georgemichael" -> "George Michael"
    # Détecte camelCase AVANT de retirer les suffixes
    if len(a) > 4 and not ' ' in a:
        has_mid_upper = any(c.isupper() for c in a[1:-1])
        if has_mid_upper:
            # Essaie de séparer sur les majuscules
            parts = []
            current = a[0]
            for c in a[1:]:
                if c.isupper() and current and current[-1].islower():
                    parts.append(current)
                    current = c
                else:
                    current += c
            if current:
                parts.append(current)
            if len(parts) >= 2:
                a = ' '.join(parts)

    # Supprimer les suffixes après | (pipe) - YouTube pattern "Artist | Vevo"
    a = re.sub(r'\s*\|.*$', '', a).strip()

    # Maintenant retire les suffixes (VEVO, Topic, etc.)
    a = _ARTIST_NOISE.sub('', a).strip()
    # Retire aussi un éventuel " - " en fin
    a = re.sub(r'\s*[-–]\s*$', '', a).strip()

    return a


def _normalize_featuring(title: str) -> str:
    """
    Corrige les featuring mal formés dans le titre.
    Exemples :
      - "Multiply  J ft.  juicy" -> "Multiply ft. Juicy J"
      - "Song Name X ft. Y" -> "Song Name ft. X Y" (réparation inversion)
    """
    import re as _re
    t = " ".join(title.split())  # normalise espaces

    # Pattern spécifique: "Nom Initiale ft. Suffixe" où l'initiale devrait être après ft.
    # Ex: "Multiply J ft. juicy" -> "Multiply ft. Juicy J"
    # Le pattern: (mot) (initiale/court) ft. (suffixe)
    ft_match = _re.search(r'(\S+)\s+(\S{1,3})\s+ft\s*\.\s*(\S+)', t, _re.IGNORECASE)
    if ft_match:
        base, initial, suffix = ft_match.groups()
        # Si l'initiale est vraiment courte (1-3 caractères) et le suffixe n'est pas "the", "a", etc
        if len(initial) <= 3 and suffix.lower() not in ['the', 'a', 'an', 'dj', 'mc']:
            # Inverser: suffixe + initiale (capitalisés)
            feat_name = f"{suffix.capitalize()} {initial.upper()}"
            t = f"{base} ft. {feat_name}"
            t = " ".join(t.split())
            print(f"[LYRICS] featuring réparé: '{title}' -> '{t}'")
            return t

    # Normaliser le format "ft." -> " ft. "
    t = _re.sub(r'\s*[Ff][Tt]\.\s*', ' ft. ', t)
    t = _re.sub(r'\s*[Ff][Ee][Aa][Tt]\.\s*', ' feat. ', t)
    t = _re.sub(r'\s+[Ff][Ee][Aa][Tt][Uu][Rr][Ii][Nn][Gg]\s+', ' featuring ', t, flags=_re.IGNORECASE)

    return " ".join(t.split())


def _clean_title(title: str) -> list[str]:
    """
    Retourne plusieurs variantes du titre à essayer :
    - tel quel (espaces normalisés)
    - sans l'année finale (ex: "Song Name 1980" -> "Song Name")
    - sans les tags entre parenthèses/crochets (ex: "Song (Remaster)" -> "Song")
    - sans les suffixes YouTube (ex: "Song | Vevo" -> "Song")
    - sans les suffixes de versions alternatives (slowed + reverb, etc.)
    """
    import re as _re
    candidates = []

    # D'abord, normaliser les featuring mal formés
    t = _normalize_featuring(title)

    # Normaliser les titres alternatifs (slowed + reverb, extended, etc.)
    try:
        import lyrics_special_cases as _lsc
        t = _lsc.normalize_alternative_title(t)
    except ImportError:
        pass

    candidates.append(t)

    # Supprimer l'année en fin (4 chiffres)
    no_year = _re.sub(r'\s+\d{4}\s*$', '', t).strip()
    if no_year and no_year != t:
        candidates.append(no_year)

    # Supprimer contenu entre parenthèses/crochets
    no_paren = _re.sub(r'\s*[\(\[][^\)\]]*[\)\]]\s*', ' ', no_year or t).strip()
    if no_paren and no_paren not in candidates:
        candidates.append(no_paren)

    # Supprimer les suffixes après | (pipe) - YouTube pattern "Song | Vevo"
    base = no_paren or no_year or t
    no_pipe = _re.sub(r'\s*\|.*$', '', base).strip()
    if no_pipe and no_pipe not in candidates:
        candidates.append(no_pipe)

    # Supprimer les suffixes après " - " (pattern "Song - Vevo")
    no_dash_suffix = _re.sub(r'\s*[-–]\s*(Topic|VEVO|Official|Music|Video|Audio|Lyrics)\s*$', '', base, flags=_re.IGNORECASE).strip()
    if no_dash_suffix and no_dash_suffix not in candidates:
        candidates.append(no_dash_suffix)

    return candidates


# ── Cache lyrics persistant (JSON) ─────────────────────────────────────────────────────
_CACHE_FILE = "lyrics_cache.json"
_cache_lock = threading.Lock()
_cache_dirty = False  # flag pour savoir si on doit sauvegarder


def _load_cache() -> dict:
    """Charge le cache depuis le fichier JSON."""
    global _cache_dirty
    import json as _json, pathlib
    path = pathlib.Path(__file__).parent / _CACHE_FILE
    try:
        if path.exists():
            data = _json.loads(path.read_text(encoding='utf-8'))
            print(f"[LYRICS] cache chargé: {len(data)} chansons")
            return data
    except Exception as e:
        print(f"[LYRICS] erreur chargement cache: {e}")
    return {}


def _save_cache(cache: dict):
    """Sauvegarde le cache vers le fichier JSON (écriture atomique via .tmp)."""
    import json as _json, pathlib, os as _os
    path = pathlib.Path(__file__).parent / _CACHE_FILE
    tmp_path = pathlib.Path(__file__).parent / (_CACHE_FILE + ".tmp")
    try:
        # Écriture dans un fichier temporaire
        tmp_path.write_text(_json.dumps(cache, ensure_ascii=False), encoding='utf-8')
        # Remplacement atomique (fonctionne sur POSIX et Windows)
        _os.replace(tmp_path, path)
        # Plus de log ici pour éviter la pollution (la sauvegarde est transparente)
    except Exception as e:
        print(f"[LYRICS] erreur sauvegarde cache: {e}")


# Cache en mémoire + lazy load
_disk_cache: dict = None


def _get_disk_cache() -> dict:
    """Retourne le cache (le charge si nécessaire)."""
    global _disk_cache
    if _disk_cache is None:
        _disk_cache = _load_cache()
    return _disk_cache


def _detect_version_suffix(title: str) -> str:
    """
    Détecte les suffixes de version dans le titre.
    Retourne le suffixe normalisé ou une chaîne vide pour la version studio standard.

    Exemples:
    - "Song Name (Live)" -> "live"
    - "Song Name - Live Version" -> "live"
    - "Song Name [Remix]" -> "remix"
    - "Song Name (Studio Version)" -> "studio"
    - "Song Name" -> "" (version standard)
    """
    if not title:
        return ""

    t = title.lower()

    # Suffixes communs à détecter (avec leurs variantes)
    version_patterns = {
        "live": ["live", "live version", "live at", "performed live", "concert", "tour"],
        "studio": ["studio", "studio version", "original", "album version"],
        "remix": ["remix", "remastered", "mix"],
        "acoustic": ["acoustic", "unplugged"],
        "radio": ["radio edit", "radio", "radio version"],
        "extended": ["extended", "extended version", "extended mix"],
        "instrumental": ["instrumental", "karaoke"],
        "demo": ["demo", "demo version"],
    }

    # Cherche d'abord les patterns entre parenthèses
    import re
    paren_match = re.search(r'\(([^)]+)\)', t)
    if paren_match:
        content = paren_match.group(1)
        for version, patterns in version_patterns.items():
            for pattern in patterns:
                if pattern in content:
                    return version

    # Cherche les patterns entre crochets
    bracket_match = re.search(r'\[([^\]]+)\]', t)
    if bracket_match:
        content = bracket_match.group(1)
        for version, patterns in version_patterns.items():
            for pattern in patterns:
                if pattern in content:
                    return version

    # Cherche les patterns après un tiret
    if " - " in t:
        parts = t.split(" - ")
        for part in parts:
            for version, patterns in version_patterns.items():
                for pattern in patterns:
                    if pattern in part:
                        return version

    # Cherche les patterns directement dans le titre
    for version, patterns in version_patterns.items():
        for pattern in patterns:
            if f" {pattern}" in t or f"{pattern} " in t:
                return version

    return ""  # Version standard (studio par défaut)


def _cache_key(artist: str, title: str) -> str:
    """Génère une clé de cache qui inclut la version de la chanson."""
    version = _detect_version_suffix(title)
    if version:
        return f"{artist.lower()}|{title.lower()}|{version}"
    return f"{artist.lower()}|{title.lower()}|studio"


def _get_cached_lyrics(artist: str, title: str) -> dict | None:
    """Retourne les lyrics depuis le cache, ou None."""
    key = _cache_key(artist, title)
    cache = _get_disk_cache()
    entry = cache.get(key)
    if entry:
        # Met à jour les stats d'écoute
        import time
        entry["last_played"] = time.time()
        entry["play_count"] = entry.get("play_count", 0) + 1
        _mark_cache_dirty()
        version = _detect_version_suffix(title)
        version_str = f" [{version}]" if version else " [studio]"
        print(f"[LYRICS] cache HIT: {artist} - {title}{version_str} (joué {entry['play_count']} fois)")
        return {
            "synced": entry.get("synced", []),
            "plain": entry.get("plain", ""),
            "offset": entry.get("offset", 0.5)
        }
    return None


def _set_cached_lyrics(artist: str, title: str, synced: list, plain: str, offset: float = 0.5):
    """Sauvegarde les lyrics dans le cache."""
    key = _cache_key(artist, title)
    cache = _get_disk_cache()
    import time
    cache[key] = {
        "artist": artist,
        "title": title,
        "synced": synced,
        "plain": plain,
        "offset": offset,
        "last_played": time.time(),
        "play_count": 1
    }
    _mark_cache_dirty()
    print(f"[LYRICS] cache SAVE: {artist} - {title}")


def _mark_cache_dirty():
    """Marque le cache comme sale (à sauvegarder)."""
    global _cache_dirty
    _cache_dirty = True


def _flush_cache_if_dirty():
    """Sauvegarde le cache si modifié."""
    global _cache_dirty
    if _cache_dirty:
        # Créer un backup avant sauvegarde (protection contre perte de données)
        _backup_cache()
        _save_cache(_get_disk_cache())
        _cache_dirty = False


def _backup_cache():
    """Crée un backup du cache actuel."""
    try:
        import shutil
        cache_file = pathlib.Path(__file__).parent / _CACHE_FILE
        if cache_file.exists():
            backup_file = cache_file.with_suffix('.json.bak')
            shutil.copy2(cache_file, backup_file)
    except Exception as e:
        pass  # Silent fail - ne pas bloquer si backup échoue


def set_song_offset(artist: str, title: str, offset: float):
    """Ajuste l'offset pour une chanson spécifique."""
    key = _cache_key(artist, title)
    cache = _get_disk_cache()
    if key in cache:
        cache[key]["offset"] = offset
        _mark_cache_dirty()
        _flush_cache_if_dirty()
        print(f"[LYRICS] offset ajusté: {artist} - {title} -> {offset}s")
    else:
        print(f"[LYRICS] impossible d'ajuster offset: chanson pas dans le cache")


def get_song_offset(artist: str, title: str) -> float | None:
    """Retourne l'offset personnalisé pour une chanson, ou None."""
    key = _cache_key(artist, title)
    cache = _get_disk_cache()
    if key in cache:
        return cache[key].get("offset")
    return None


# Anciens noms pour compatibilité
def _get_cached(artist: str, title: str) -> dict | None:
    return _get_cached_lyrics(artist, title)


def _set_cached(artist: str, title: str, result: dict | None):
    if result:
        # PRÉSERVER L'OFFSET si présent dans le résultat
        offset = result.get("offset", 0.5)
        # Si le résultat contient un offset, le garder
        # Sinon, essayer de charger l'offset existant du cache
        if offset == 0.5:  # Valeur par défaut
            cache = _get_disk_cache()
            key = _cache_key(artist, title)
            if key in cache and "offset" in cache[key]:
                offset = cache[key]["offset"]
        _set_cached_lyrics(artist, title, result.get("synced", []), result.get("plain", ""), offset)

# ── Fetch lyrics ──────────────────────────────────────────────────────────────

def _lrclib_get(artist: str, title: str, timeout: float = 12.0):  # Timeout augmenté (lrclib est lent)
    """lrclib.net API (gratuit, sync+plain).

    Stratégie pour MAXIMISER les paroles synchronisées :
      1) /api/get  -> correspondance exacte artiste/titre (renvoie le LRC sync
         le plus fiable quand il existe).
      2) /api/search -> en secours, mais on ne prend PAS bêtement le premier
         résultat : on privilégie une version qui a réellement `syncedLyrics`,
         puis à défaut une version plain.
    """
    import urllib.request, urllib.parse, urllib.error, json as _json
    headers = {"User-Agent": "BoxScreen (https://github.com/) lyrics-sync"}
    plain_fallback = None

    def _has_synced(entry):
        return bool(entry and entry.get("syncedLyrics"))

    # 1) /api/get : correspondance exacte (meilleur taux de sync)
    try:
        params = urllib.parse.urlencode({
            "artist_name": artist,
            "track_name": title,
        })
        req = urllib.request.Request(f"https://lrclib.net/api/get?{params}", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            entry = _json.loads(r.read().decode())
            if _has_synced(entry):
                print("[LYRICS]   lrclib /get: version synchronisée trouvée")
                return entry
            # /get a répondu mais sans sync -> on garde en réserve, on tente search
            if entry and entry.get("plainLyrics"):
                plain_fallback = entry
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"[LYRICS]   lrclib /get error: {e.code}")
    except Exception as e:
        print(f"[LYRICS]   lrclib /get error: {e}")

    # 2) /api/search : on privilégie la première version AVEC syncedLyrics
    q = f"{artist} {title}".strip()
    params = urllib.parse.urlencode({"q": q})
    try:
        req = urllib.request.Request(f"https://lrclib.net/api/search?{params}", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            results = _json.loads(r.read().decode())
            if isinstance(results, list) and results:
                synced = next((x for x in results if _has_synced(x)), None)
                if synced:
                    print(f"[LYRICS]   lrclib /search: sync trouvé parmi {len(results)} résultats")
                    return synced
                # aucune version synchronisée -> premier plain dispo
                plain = next((x for x in results if x.get("plainLyrics")), results[0])
                return plain
    except Exception as e:
        print(f"[LYRICS]   lrclib /search error: {e}")

    # 3) Rien de mieux que le plain de /get s'il existait
    return plain_fallback


def _lyrics_ovh_get(artist: str, title: str, timeout: float = 1.5):  # 2s -> 1.5s
    """lyrics.ovh API (gratuit, plain only)."""
    import urllib.request, json as _json
    try:
        a_enc = urllib.parse.quote(artist)
        t_enc = urllib.parse.quote(title)
        req = urllib.request.Request(f"https://api.lyrics.ovh/v1/{a_enc}/{t_enc}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = _json.loads(r.read().decode())
            if data.get("lyrics"):
                return {"plainLyrics": data["lyrics"], "syncedLyrics": ""}
    except Exception:
        pass
    return None


def _genius_fetch(artist: str, title: str):
    """Genius.com via recherche HTML (fallback, plus complet mais plus lent)."""
    import urllib.request, urllib.parse, json as _json, re as _re
    try:
        # Recherche Genius API avec User-Agent valide
        q = f"{artist} {title}".strip()
        params = urllib.parse.urlencode({"q": q})
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        req = urllib.request.Request(
            f"https://genius.com/api/search/multi?{params}",
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = _json.loads(r.read().decode())
            hits = data.get("response", {}).get("sections", [])
            for section in hits:
                for hit in section.get("hits", []):
                    if hit.get("type") == "song":
                        path = hit.get("result", {}).get("path")
                        if path:
                            # Récupère les lyrics depuis la page
                            url = f"https://genius.com{path}"
                            req2 = urllib.request.Request(url, headers=headers)
                            with urllib.request.urlopen(req2, timeout=5) as r2:
                                html = r2.read().decode()
                                # Genius met les lyrics dans <div data-lyrics="true">
                                match = _re.search(r'<div data-lyrics="true">([^<]+)</div>', html, _re.DOTALL)
                                if not match:
                                    # Fallback: cherche dans le JSON embedded
                                    match = _re.search(r'"content":"((?:[^"\\]|\\.)*)"', html)
                                if match:
                                    # Unescape JSON
                                    lyrics = match.group(1).replace('\\n', '\n').replace('\\"', '"').replace('\\/', '/')
                                    if len(lyrics) > 100:  # sanity check
                                        return {"plainLyrics": lyrics, "syncedLyrics": ""}
    except Exception as e:
        pass
    return None


def _chartlyrics_get(artist: str, title: str, timeout: float = 2.0):
    """ChartLyrics API (gratuit, nécessite parsing)."""
    import urllib.request, urllib.parse
    try:
        params = urllib.parse.urlencode({"artist": artist, "song": title})
        req = urllib.request.Request(f"http://api.chartlyrics.com/apiv1.asmx/SearchLyricDirect?{params}", timeout=timeout)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # ChartLyrics retourne XML, on extrait le Lyric
            import xml.etree.ElementTree as ET
            content = r.read().decode()
            root = ET.fromstring(content)
            # Cherche <Lyric>...</Lyric>
            lyric_elem = root.find('.//Lyric')
            if lyric_elem is not None and lyric_elem.text:
                return {"plainLyrics": lyric_elem.text, "syncedLyrics": ""}
    except Exception:
        pass
    return None


# ── YouTube OAuth2 ─────────────────────────────────────────────────────────────
_YOUTUBE_OAUTH_FILE = "youtube_oauth.json"
_YOUTUBE_CLIENT_ID = "36717679904-4a4tq23ooo123dg348f6k3jens68mlq6.apps.googleusercontent.com"
_YOUTUBE_CLIENT_SECRET = "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"

# ── Quota backoff ───────────────────────────────────────────────────────────────
# La YouTube Data API a un quota journalier (10 000 unités par défaut) qui se
# réinitialise à minuit, heure du Pacifique (PT). Une fois un 403 "quota
# exceeded" rencontré, inutile de retenter avant le reset : on persiste un
# timestamp d'expiration sur disque pour désactiver tout appel OAuth d'ici là.
_QUOTA_BACKOFF_FILE = "youtube_quota_backoff.json"
_quota_blocked_until = 0.0  # epoch seconds (time.time) ; 0 = pas bloqué


def _pacific_midnight_epoch_after(now_epoch: float) -> float:
    """Renvoie l'epoch du prochain minuit Pacifique (reset du quota YouTube)."""
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime, timedelta
        pt = ZoneInfo("America/Los_Angeles")
        now_pt = datetime.fromtimestamp(now_epoch, pt)
        next_midnight = (now_pt + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return next_midnight.timestamp()
    except Exception:
        # Fallback grossier : +24h (PT ~ UTC-7/-8, l'imprécision est sans risque)
        return now_epoch + 24 * 3600


def _load_quota_backoff() -> float:
    """Charge le timestamp de blocage quota depuis le disque (0 si aucun/expiré)."""
    global _quota_blocked_until
    import json as _json, os as _os
    try:
        if _os.path.exists(_QUOTA_BACKOFF_FILE):
            with open(_QUOTA_BACKOFF_FILE) as f:
                _quota_blocked_until = float(_json.load(f).get("blocked_until", 0))
    except Exception:
        _quota_blocked_until = 0.0
    return _quota_blocked_until


def _quota_is_blocked() -> bool:
    """True si on est encore dans la fenêtre de backoff quota."""
    global _quota_blocked_until
    if _quota_blocked_until == 0.0:
        _load_quota_backoff()
    if _quota_blocked_until and time.time() < _quota_blocked_until:
        return True
    if _quota_blocked_until and time.time() >= _quota_blocked_until:
        _quota_blocked_until = 0.0  # fenêtre passée, on réactive
    return False


def _set_quota_blocked():
    """Marque le quota comme épuisé jusqu'au prochain reset PT, persiste sur disque."""
    global _quota_blocked_until
    import json as _json
    _quota_blocked_until = _pacific_midnight_epoch_after(time.time())
    try:
        with open(_QUOTA_BACKOFF_FILE, "w") as f:
            _json.dump({"blocked_until": _quota_blocked_until}, f)
    except Exception:
        pass
    try:
        from datetime import datetime
        when = datetime.fromtimestamp(_quota_blocked_until).strftime("%H:%M")
        print(f"[LYRICS]   ⏸ quota YouTube épuisé - OAuth désactivé jusqu'au reset (~{when})")
    except Exception:
        print("[LYRICS]   ⏸ quota YouTube épuisé - OAuth désactivé jusqu'au reset")


def _get_youtube_credentials():
    """Get OAuth2 credentials for YouTube Data API."""
    import json as _json
    import os as _os
    from google.oauth2.credentials import Credentials
    import google.auth.transport.requests

    # Load existing credentials if available
    if _os.path.exists(_YOUTUBE_OAUTH_FILE):
        try:
            with open(_YOUTUBE_OAUTH_FILE, 'r') as f:
                token_data = _json.load(f)
            creds = Credentials.from_authorized_user_info(token_data)
            # Refresh if expired
            if creds.expired and creds.refresh_token:
                creds.refresh(google.auth.transport.requests.Request())
                # Save refreshed token
                with open(_YOUTUBE_OAUTH_FILE, 'w') as f:
                    f.write(creds.to_json())
            return creds
        except Exception as e:
            print(f"[LYRICS] OAuth2 load error: {e}")

    # No credentials, need to authorize
    print(f"[LYRICS] YouTube OAuth2 authorization required...")
    print(f"[LYRICS] Please run: python -c \"from lyrics_source import _youtube_auth_setup; _youtube_auth_setup()\"")
    return None


def _youtube_auth_setup():
    """Setup YouTube OAuth2 credentials using local server flow."""
    import json as _json
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
    PORT = 8888  # Fixed port - must be registered in Google Cloud Console

    print(f"[LYRICS] Using port {PORT} for OAuth2...")

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": _YOUTUBE_CLIENT_ID,
                "client_secret": _YOUTUBE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [f"http://127.0.0.1:{PORT}"]
            }
        },
        scopes=SCOPES
    )

    print("[LYRICS] Opening browser for OAuth2 authorization...")
    print(f"[LYRICS] Make sure http://127.0.0.1:{PORT} is added in Google Cloud Console!")
    creds = flow.run_local_server(host='127.0.0.1', port=PORT, open_browser=True)

    # Save credentials
    with open(_YOUTUBE_OAUTH_FILE, 'w') as f:
        f.write(creds.to_json())

    print(f"[LYRICS] OAuth2 credentials saved to {_YOUTUBE_OAUTH_FILE}")
    return creds


def _fetch_youtube_captions_oauth(video_id: str, creds) -> dict | None:
    """Fetch captions using YouTube Data API with OAuth2."""
    try:
        import urllib.request
        import urllib.error
        import json as _json
        import re as _re

        # Get access token
        if creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())

        access_token = creds.token
        print(f"[LYRICS]   OAuth2 token valid: {not creds.expired}, scopes: {creds.scopes}")

        # Get available captions
        url = f"https://www.googleapis.com/youtube/v3/captions?part=snippet&videoId={video_id}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "BoxScreen/1.0",
            "Authorization": f"Bearer {access_token}"
        })

        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                data = _json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            print(f"[LYRICS]   List captions error: {e.code} - {error_body[:300]}")
            # 403 + "quota" => quota journalier épuisé : on coupe l'OAuth jusqu'au reset
            if e.code == 403 and "quota" in error_body.lower():
                _set_quota_blocked()
            return None

        if not data.get("items"):
            print(f"[LYRICS]   No captions found")
            return None

        # Find English caption (prefer manual over auto)
        caption_id = None
        lang_code = None

        for track_lang in ['en', 'fr']:
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                if snippet.get("languageCode") == track_lang:
                    caption_id = item.get("id")
                    lang_code = track_lang
                    # Prefer non-auto
                    if snippet.get("trackKind") != "asr":
                        break
            if caption_id:
                break

        # Fallback to any available
        if not caption_id and data.get("items"):
            first = data["items"][0]
            caption_id = first.get("id")
            lang_code = first.get("snippet", {}).get("languageCode", "unknown")

        if not caption_id:
            print(f"[LYRICS]   No suitable caption track")
            return None

        print(f"[LYRICS]   Found caption: {lang_code}, downloading...")

        # Download caption content (SRT format)
        download_url = f"https://www.googleapis.com/youtube/v3/captions/{caption_id}?tfmt=srt"
        print(f"[LYRICS]   Download URL: {download_url[:50]}...")
        req2 = urllib.request.Request(download_url, headers={
            "User-Agent": "BoxScreen/1.0",
            "Authorization": f"Bearer {access_token}"
        })

        try:
            with urllib.request.urlopen(req2, timeout=10) as response:
                print(f"[LYRICS]   Response status: {response.status}")
                content = response.read().decode('utf-8-sig')
                print(f"[LYRICS]   Content length: {len(content)} chars")
        except urllib.error.HTTPError as e:
            # Read error response for more details
            error_body = e.read().decode()
            print(f"[LYRICS]   Caption download HTTP {e.code}: {error_body[:400]}")
            return None

        # Parse SRT format
        synced = _parse_srt_content(content)

        if synced:
            plain = '\n'.join(text for _, text in synced)
            print(f"[LYRICS] OK YouTube OAuth2: {len(synced)} lines")
            return {"synced": synced, "plain": plain}
        else:
            print(f"[LYRICS]   Could not parse SRT")
    except Exception as e:
        print(f"[LYRICS]   YouTube OAuth2 error: {e}")
    return None


def _parse_srt_content(content: str) -> list:
    """Parse SRT format content into (timestamp, text) tuples."""
    import re as _re
    synced = []

    # SRT format:
    # 1
    # 00:00:00,000 --> 00:00:03,000
    # Text here
    # <blank line>

    lines = content.strip().split('\n')
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Look for timestamp line: 00:00:00,000 --> 00:00:03,000
        ts_match = _re.match(r'(\d+):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2}),(\d{3})', line)
        if ts_match:
            # Use start time
            hours = int(ts_match.group(1))
            minutes = int(ts_match.group(2))
            seconds = int(ts_match.group(3))
            millis = int(ts_match.group(4))
            timestamp = hours * 3600 + minutes * 60 + seconds + millis / 1000.0

            # Next lines are the text until empty line or next number
            i += 1
            text_parts = []
            while i < len(lines):
                next_line = lines[i].strip()
                # Stop at empty line or next subtitle number
                if not next_line or next_line.isdigit():
                    break
                # Skip timestamps
                if _re.match(r'\d+:\d{2}:\d{2},\d{3}', next_line):
                    i += 1
                    continue
                text_parts.append(next_line)
                i += 1

            text = ' '.join(text_parts).strip()
            # Clean up artifacts
            text = _re.sub(r'\[.*?\]', '', text)
            text = _re.sub(r'♪', '', text)
            text = text.strip()

            if text:
                synced.append((timestamp, text))

        else:
            i += 1

    return synced


_CAPTION_NOISE_RE = re.compile(
    '|'.join([
        r'^\[[^\]]+\]$',  # [Anything in brackets]
        r'^♪+$',           # Musical notes only
        r'^\s+$',          # Whitespace only
    ]),
    re.IGNORECASE,
)


def _clean_caption_line(text: str) -> str:
    """Nettoie une ligne de sous-titre (crochets, notes de musique)."""
    text = re.sub(r'\[.*?\]', '', text)  # Remove bracketed content
    text = re.sub(r'♪', '', text)         # Remove musical notes
    return text.strip()


def _entries_to_lyrics(entries, source: str) -> dict | None:
    """Convertit [(start, text), ...] en dict {synced, plain}, filtre le bruit."""
    synced = []
    for ts, text in entries:
        text = (text or '').strip()
        if not text or _CAPTION_NOISE_RE.match(text):
            continue
        text = _clean_caption_line(text)
        if text:
            synced.append((ts, text))

    if synced:
        plain = '\n'.join(t for _, t in synced)
        print(f"[LYRICS] OK YouTube captions ({source}): {len(synced)} lines")
        return {"synced": synced, "plain": plain}
    print(f"[LYRICS]   All lines were filtered as noise ({source})")
    return None


def _get_cookies_file() -> str | None:
    """Retourne le chemin du cookies.txt du projet (priorité absolue)."""
    import pathlib
    cookies_path = pathlib.Path(__file__).parent / "cookies.txt"
    if cookies_path.exists():
        return str(cookies_path)
    return None


def _fetch_captions_transcript_api(video_id: str) -> dict | None:
    """Récupère les sous-titres via youtube-transcript-api (pas d'OAuth)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        import os

        print(f"[LYRICS]   -> Trying youtube-transcript-api...")

        # PRIORITÉ ABSOLUE: cookies.txt du projet pour éviter le blocage IP
        cookies_file = _get_cookies_file() or os.environ.get("YTDLP_COOKIES_FILE")
        api = None
        if cookies_file and os.path.exists(cookies_file):
            try:
                import requests
                from http.cookiejar import MozillaCookieJar
                jar = MozillaCookieJar(cookies_file)
                jar.load(ignore_discard=True, ignore_expires=True)
                sess = requests.Session()
                sess.cookies = jar
                api = YouTubeTranscriptApi(http_client=sess)
                print(f"[LYRICS]   transcript-api: [OK] cookies file = {cookies_file}")
            except TypeError:
                # Ancienne signature: pas de http_client -> fallback cookie_path
                try:
                    api = YouTubeTranscriptApi(cookie_path=cookies_file)
                    print(f"[LYRICS]   transcript-api: [OK] cookie_path = {cookies_file}")
                except Exception:
                    api = None
            except Exception as ce:
                print(f"[LYRICS]   transcript-api cookies error: {str(ce)[:60]}")
                api = None
        if api is None:
            print(f"[LYRICS]   transcript-api: [WARN]  sans cookies (IP risk)")
            api = YouTubeTranscriptApi()

        transcripts = None
        for lang in ['en', 'fr', 'es', 'de', 'it', 'pt', 'ja', 'ko', 'zh']:
            try:
                transcripts = api.fetch(video_id, languages=[lang])
                print(f"[LYRICS]   Found captions in language: {lang}")
                break
            except Exception:
                continue

        if not transcripts:
            try:
                transcripts = api.fetch(video_id)
                print(f"[LYRICS]   Found captions (default language)")
            except Exception as e:
                error_str = str(e)
                if "RequestBlocked" in error_str or "IPBlocked" in error_str:
                    print(f"[LYRICS]   YouTube blocking requests (IP blocked)")
                else:
                    print(f"[LYRICS]   No captions: {error_str[:60]}")
                return None

        raw = transcripts.to_raw_data() if transcripts else None
        if not raw:
            print(f"[LYRICS]   Empty transcript")
            return None

        entries = [(e.get('start', 0), e.get('text', '')) for e in raw]
        return _entries_to_lyrics(entries, "transcript-api")
    except Exception as e:
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        print(f"[LYRICS]   youtube-transcript-api error: {e}")
    return None


def _parse_vtt(vtt_text: str) -> list:
    """Parse un fichier WebVTT en [(start_seconds, text), ...]."""
    entries = []
    ts_re = re.compile(
        r'(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*'
        r'(\d{2}):(\d{2}):(\d{2})[.,](\d{3})'
    )
    blocks = re.split(r'\n\s*\n', vtt_text)
    seen = set()
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        start = None
        text_lines = []
        for line in lines:
            m = ts_re.search(line)
            if m:
                h, mn, s, ms = int(m[1]), int(m[2]), int(m[3]), int(m[4])
                start = h * 3600 + mn * 60 + s + ms / 1000.0
            elif line.strip() and not line.strip().isdigit() \
                    and not line.startswith('WEBVTT') \
                    and not line.startswith('Kind:') \
                    and not line.startswith('Language:'):
                # Remove inline VTT tags like <00:00:01.000><c> ... </c>
                clean = re.sub(r'<[^>]+>', '', line).strip()
                if clean:
                    text_lines.append(clean)
        if start is not None and text_lines:
            text = ' '.join(text_lines)
            # yt-dlp auto-subs repeat lines across cues; dedupe consecutive
            if text not in seen:
                entries.append((start, text))
                seen.add(text)
    return entries


def _fetch_captions_ytdlp(video_id: str) -> dict | None:
    """Dernier recours: extrait les sous-titres via yt-dlp (auto-subs inclus)."""
    import tempfile, os, glob, subprocess, sys

    try:
        import yt_dlp  # noqa: F401
    except Exception:
        print(f"[LYRICS]   yt-dlp not installed, skipping")
        return None

    print(f"[LYRICS]   -> Trying yt-dlp...")
    url = f"https://www.youtube.com/watch?v={video_id}"
    # Patterns regex: YouTube nomme parfois les pistes "en-<id-vidéo>"
    # (ex: en-nP7-2PuUl7o). 'en.*' matche toutes les variantes anglaises,
    # idem pour les autres langues. Un 'en' nu seul ne matcherait rien.
    langs = "en.*,fr.*,es.*,de.*,it.*,pt.*,ja.*,ko.*,zh.*"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            out_tmpl = os.path.join(tmp, "%(id)s.%(ext)s")
            cmd = [
                sys.executable, "-m", "yt_dlp",
                "--skip-download",
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs", langs,
                "--sub-format", "vtt",
                # Télécharge le solveur de challenges JS de YouTube (deno).
                # Sans ça: "Signature solving failed" -> aucun sous-titre.
                "--remote-components", "ejs:github",
                "-o", out_tmpl,
                url,
            ]
            # Auth via cookies du navigateur pour contourner le blocage IP.
            # PRIORITÉ ABSOLUE: cookies.txt du projet, puis env vars.
            cookies_file = _get_cookies_file() or os.environ.get("YTDLP_COOKIES_FILE")
            cookies_browser = os.environ.get("YTDLP_COOKIES_BROWSER", "chrome")
            if cookies_file and os.path.exists(cookies_file):
                cmd[-1:-1] = ["--cookies", cookies_file]
                print(f"[LYRICS]   yt-dlp: [OK] cookies file = {cookies_file}")
            elif cookies_browser and cookies_browser.lower() != "none":
                cmd[-1:-1] = ["--cookies-from-browser", cookies_browser]
                print(f"[LYRICS]   yt-dlp: cookies-from-browser = {cookies_browser}")
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=20  # 60s -> 20s (plus rapide)
            )
            if proc.returncode != 0:
                err = (proc.stderr or "").strip()[:120]
                print(f"[LYRICS]   yt-dlp error: {err}")

            vtt_files = sorted(glob.glob(os.path.join(tmp, "*.vtt")))
            if not vtt_files:
                print(f"[LYRICS]   yt-dlp found no subtitles")
                return None

            # Prefer non-auto subs (filename without 'auto') if present
            vtt_files.sort(key=lambda f: ('auto' in os.path.basename(f).lower(), f))
            with open(vtt_files[0], encoding='utf-8') as f:
                vtt_text = f.read()

            entries = _parse_vtt(vtt_text)
            if not entries:
                print(f"[LYRICS]   yt-dlp: empty after parse")
                return None
            return _entries_to_lyrics(entries, "yt-dlp")
    except subprocess.TimeoutExpired:
        print(f"[LYRICS]   yt-dlp timeout")
    except Exception as e:
        print(f"[LYRICS]   yt-dlp error: {e}")
    return None


def _fetch_youtube_captions(video_id: str) -> dict | None:
    """Récupère les sous-titres YouTube.

    Ordre: transcript-api (fiable, vidéos tierces) -> yt-dlp (si IP bloquée
    ou auto-subs) -> OAuth2 (seulement utile pour tes propres vidéos).
    """
    print(f"[LYRICS]   Fetching captions for video_id: {video_id}")

    # 1) youtube-transcript-api en priorité (pas d'OAuth, marche sur vidéos tierces)
    result = _fetch_captions_transcript_api(video_id)
    if result:
        return result

    # 2) yt-dlp en secours (IP blocking, auto-subs)
    print(f"[LYRICS]   -> transcript-api failed, trying yt-dlp...")
    result = _fetch_captions_ytdlp(video_id)
    if result:
        return result

    # 3) OAuth2 en dernier (utile uniquement si la vidéo t'appartient).
    #    On saute si le quota est déjà épuisé : inutile de brûler un appel.
    if _quota_is_blocked():
        print(f"[LYRICS]   -> OAuth2 sauté (quota YouTube épuisé jusqu'au reset)")
        return None
    creds = _get_youtube_credentials()
    if creds:
        print(f"[LYRICS]   -> yt-dlp failed, trying YouTube OAuth2...")
        result = _fetch_youtube_captions_oauth(video_id, creds)
        if result:
            return result
        print(f"[LYRICS]   -> OAuth2 failed too")

    return None


def _result_to_lyrics(data: dict) -> dict | None:
    """Convertit un résultat lrclib en dict interne, ou None si vide."""
    synced = data.get("syncedLyrics") or ""
    plain  = data.get("plainLyrics")  or ""

    if synced:
        return {"synced": _parse_lrc(synced), "plain": plain}

    if plain:
        plain_lines = [(None, l) for l in plain.splitlines() if l.strip()]

        # Essaie de créer des timestamps estimés avec le BPM
        try:
            import bpm_source as _bs
            bpm = _bs.get_bpm()
            if bpm and bpm > 0:
                synced_with_bpm = _create_estimated_sync([l for _, l in plain_lines], bpm)
                print(f"[LYRICS] 🎵 plain lyrics + BPM {bpm:.0f} -> sync estimé")
                return {"synced": synced_with_bpm, "plain": plain}
        except Exception:
            pass

        return {"synced": plain_lines, "plain": plain}

    return None


# ── Scoring de qualité des lyrics ─────────────────────────────────────────────────────
# Le système de scoring évalue plusieurs critères pour choisir la meilleure source
# de paroles parmi lrclib, YouTube captions et autres APIs.

def _normalize_for_match(text: str) -> str:
    """
    Normalise un texte pour la comparaison floue (titre/artiste).
    - lowercase
    - sans feat./prod./remaster/parenthèses
    - sans espaces multiples
    """
    import re as _re
    if not text:
        return ""
    t = text.lower()

    # Retire tout entre parenthèses/crochets (d'abord!)
    t = _re.sub(r'[\(\[][^\)\]]*[\)\]]', '', t)

    # Retire featuring, prod., etc (tout ce qui suit)
    t = _re.sub(r'\s*[Ff][Tt]\.\s+.*$', '', t)
    t = _re.sub(r'\s*[Ff]eat(?:uring)?\.?\s+.*$', '', t)
    t = _re.sub(r'\s*[Pp]rod(?:uced)?\.?\s+(?:by\s+)?$', '', t)
    t = _re.sub(r'\s*[Pp]rod(?:uced)?\.?\s+(?:by\s+)?.*$', '', t)

    # Retire les suffixes courants après tiret (Radio Edit, Remastered, Topic, etc.)
    suffixes = ['radio edit', 'remastered', 'remaster', 'explicit', 'clean',
                'bonus track', 'album version', 'single version', 'topic',
                'official', 'vevo', 'music', 'video', 'hd', 'lyrics']
    for suffix in suffixes:
        t = _re.sub(r'\s*[-–]\s*' + _re.escape(suffix) + r'\b.*$', '', t)

    # Normalise les espaces
    t = ' '.join(t.split())
    return t


def _fuzzy_match(a: str, b: str) -> float:
    """
    Similarité floue entre deux chaînes (0.0 à 1.0).
    Utilise SequenceMatcher de difflib.
    """
    from difflib import SequenceMatcher as _SM
    if not a or not b:
        return 0.0
    na, nb = _normalize_for_match(a), _normalize_for_match(b)
    if not na or not nb:
        return 0.0
    return _SM(None, na, nb).ratio()


def _detect_caption_auto_generated(synced: list) -> tuple[bool, str]:
    """
    Détecte si les captions semblent auto-générées par YouTube.
    Retourne (is_auto, language_code).

    Signes d'auto-generation:
    - Pas de ponctuation ou très peu
    - Tout en minuscules
    - Mots collés (sans espaces)
    - [Music], [Applause], ♪ fréquent
    """
    if not synced or len(synced) < 3:
        return False, ""

    text_samples = [text for _, text in synced[-10:]]  # Dernières lignes
    all_text = ' '.join(text_samples)

    # Compte la ponctuation
    punct_count = sum(1 for c in all_text if c in '.,!?;:')
    punct_ratio = punct_count / len(all_text) if all_text else 0

    # Vérifie si tout est en minuscules
    has_upper = any(c.isupper() for c in all_text if c.isalpha())

    # Mots collés (signe de mauvaise transcription)
    # Un mot "collé" contient plus de 20 caractères sans espace
    has_collapsed = any(len(word) > 20 for word in all_text.split())

    # Signes de bruit (crochets, notes)
    noise_count = sum(1 for line in text_samples if '[' in line or '♪' in line)
    noise_ratio = noise_count / len(text_samples) if text_samples else 0

    # Détection langage (rudimentaire)
    lang = "unknown"
    # Anglais: mots courants
    en_words = ['the', 'and', 'is', 'in', 'to', 'of', 'you', 'that']
    fr_words = ['le', 'la', 'les', 'et', 'est', 'en', 'que', 'qui']
    words = all_text.lower().split()
    en_count = sum(1 for w in words if w in en_words)
    fr_count = sum(1 for w in words if w in fr_words)
    if en_count > 3:
        lang = "en"
    elif fr_count > 3:
        lang = "fr"

    # Heuristique d'auto-generation
    is_auto = (
        punct_ratio < 0.02 or  # Moins de 2% de ponctuation
        not has_upper or       # Pas de majuscules
        has_collapsed or       # Mots collés
        noise_ratio > 0.3      # Plus de 30% de lignes avec bruit
    )

    return is_auto, lang


def _check_temporal_coherence(synced: list, media_duration: float | None) -> tuple[bool, str]:
    """
    Vérifie la cohérence temporelle des timestamps.
    Retourne (is_coherent, reason).

    Critères:
    - Timestamps strictement croissants
    - Premier timestamp pas absurde (< 60s sauf intro longue)
    - Dernier timestamp ≤ durée du média
    - Densité raisonnable (ni 5 lignes pour 4 min, ni 400)
    """
    if not synced or len(synced) < 2:
        return True, ""

    # Vérifie croissance stricte
    timestamps = [ts for ts, _ in synced if ts is not None]
    if len(timestamps) != len(synced):
        return False, "certains timestamps manquent"

    for i in range(len(timestamps) - 1):
        if timestamps[i] >= timestamps[i + 1]:
            return False, f"timestamps non croissants: {timestamps[i]} >= {timestamps[i + 1]}"

    first_ts = timestamps[0]
    last_ts = timestamps[-1]

    # Premier timestamp pas absurde
    if first_ts > 60:
        return False, f"premier timestamp trop tard: {first_ts}s"

    # Dernier timestamp vs durée
    if media_duration and last_ts > media_duration + 5:
        return False, f"dernier timestamp > durée: {last_ts}s > {media_duration}s"

    # Densité raisonnable
    if media_duration:
        duration = last_ts - first_ts
        density = len(synced) / duration if duration > 0 else 0
        # Moins de 0.05 ligne/sec (1 ligne / 20s) ou plus de 2 lignes/sec
        if density < 0.05:
            return False, f"densité trop faible: {len(synced)} lignes en {duration:.0f}s"
        if density > 2.5:
            return False, f"densité trop élevée: {len(synced)} lignes en {duration:.0f}s"

    return True, ""


def score_lyrics_candidate(
    candidate: dict,
    artist: str,
    title: str,
    media_duration: float | None = None,
    video_id: str | None = None,
) -> tuple[float, dict]:
    """
    Score un candidat de lyrics selon plusieurs critères.

    Args:
        candidate: dict avec "synced", "plain", et optionnellement
                   "trackName", "artistName", "duration", "source"
        artist: artiste attendu
        title: titre attendu
        media_duration: durée du média en secondes (si connue)
        video_id: ID YouTube (si dispo)

    Returns:
        (score: float, details: dict)
        score: 0.0 à 100.0, plus élevé = meilleur
        details: dict avec les sous-scores et raison
    """
    details = {
        "sync_bonus": 0,
        "metadata_match": 0,
        "metadata_penalty": 0,
        "temporal_score": 0,
        "text_quality": 0,
        "source_bonus": 0,
        "total": 0,
        "reason": [],
    }

    if not candidate or not candidate.get("plain"):
        details["reason"].append("pas de texte")
        return 0.0, details

    total = 0.0

    # ── 1. SYNC : syncedLyrics > plain ─────────────────────────────────────
    synced = candidate.get("synced", [])
    has_sync = bool(synced) and len(synced) > 0 and synced[0][0] is not None

    if has_sync:
        details["sync_bonus"] = 30  # Gros bonus pour sync
        total += 30
        details["reason"].append(f"[V] sync ({len(synced)} lignes)")
    else:
        details["reason"].append("[X] plain only")

    # ── 2. MATCH MÉTADONNÉES ────────────────────────────────────────────────
    # lrclib renvoie trackName/artistName/duration
    cand_artist = candidate.get("artistName") or candidate.get("artist")
    cand_title = candidate.get("trackName") or candidate.get("title")
    cand_duration = candidate.get("duration")

    artist_match = 0.0
    title_match = 0.0

    if cand_artist:
        artist_match = _fuzzy_match(cand_artist, artist)
    if cand_title:
        title_match = _fuzzy_match(cand_title, title)

    # Score de match (0-15)
    metadata_score = (artist_match * 7 + title_match * 8)
    details["metadata_match"] = metadata_score
    total += metadata_score

    if artist_match > 0.8 and title_match > 0.8:
        details["reason"].append(f"[V] métadonnées match (artist:{artist_match:.0%}, title:{title_match:.0%})")
    elif artist_match > 0.5 or title_match > 0.5:
        details["reason"].append(f"~ match partiel (artist:{artist_match:.0%}, title:{title_match:.0%})")

    # Pénalité durée si écart > 7s
    if cand_duration and media_duration:
        duration_diff = abs(cand_duration - media_duration)
        if duration_diff > 7:
            penalty = min(20, int(duration_diff / 5))  # -4 points par 5s de diff
            details["metadata_penalty"] = -penalty
            total -= penalty
            details["reason"].append(f"[X] durée écart {duration_diff:.0f}s (pénalité -{penalty})")

    # ── 3. COHÉRENCE TEMPORELLE ────────────────────────────────────────────
    if has_sync:
        is_coherent, reason = _check_temporal_coherence(synced, media_duration)
        if is_coherent:
            details["temporal_score"] = 10
            total += 10
            details["reason"].append("[V] timestamps cohérents")
        else:
            details["temporal_score"] = 0
            details["reason"].append(f"[X] {reason}")

    # ── 4. QUALITÉ TEXTE ────────────────────────────────────────────────────
    text_quality_score = 0

    # Détecte auto-generation YouTube
    source = candidate.get("source", "")
    is_youtube = "youtube" in source.lower() or video_id and not candidate.get("artistName")

    if is_youtube and has_sync:
        is_auto, lang = _detect_caption_auto_generated(synced)
        if is_auto:
            text_quality_score = 0
            details["reason"].append("[X] captions auto-générés (pauvres)")
        else:
            text_quality_score = 15
            details["reason"].append("[V] captions uploadées (bonne qualité)")
    elif has_sync:
        text_quality_score = 10
        details["reason"].append("[V] sync LRC (bonne qualité)")

    details["text_quality"] = text_quality_score
    total += text_quality_score

    # ── 5. SOURCE BONUS ─────────────────────────────────────────────────────
    # lrclib a des métadonnées fiables
    if candidate.get("artistName") and candidate.get("trackName"):
        details["source_bonus"] = 5
        total += 5

    details["total"] = total
    return total, details


def _compare_candidates_cross(
    candidates: list[dict],
    artist: str,
    title: str,
) -> list[tuple[int, int, float]]:
    """
    Compare les candidats par similarité de texte.
    Retourne liste de (i, j, similarity) pour les paires avec similarité > 0.5.
    """
    from difflib import SequenceMatcher as _SM

    comparisons = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            c1, c2 = candidates[i], candidates[j]
            p1, p2 = c1.get("plain", ""), c2.get("plain", "")
            if not p1 or not p2:
                continue

            # Similarité globale
            sim = _SM(None, p1, p2).ratio()

            # Similarité par lignes communes (si sync)
            s1, s2 = c1.get("synced", []), c2.get("synced", [])
            if s1 and s2 and s1[0][0] is not None and s2[0][0] is not None:
                # Prend les lignes aux timestamps similaires
                lines1 = [text for _, text in s1]
                lines2 = [text for _, text in s2]
                common_ratio = len(set(lines1) & set(lines2)) / max(len(set(lines1)), len(set(lines2))) if set(lines1) | set(lines2) else 0
                if common_ratio > 0.5:
                    comparisons.append((i, j, common_ratio))

            if sim > 0.5:
                comparisons.append((i, j, sim))

    return comparisons


def _fetch_lyrics(artist: str, title: str) -> dict | None:
    """Cherche lyrics - plusieurs stratégies, fallback APIs."""
    # Enregistre la clé de recherche pour détecter les changements
    global _current_search_key
    search_key = f"{artist.lower()}|{title.lower()}"
    _current_search_key = search_key

    # ── Cas particuliers : détection automatique ────────────────────────────────
    try:
        import lyrics_special_cases as _lsc
        case_analysis = _lsc.analyze_track(artist, title)

        # Skip instrumental/DJ mix
        if case_analysis.get("should_skip"):
            reason = case_analysis.get("skip_reason", "")
            print(f"[LYRICS] [SKIP]  Skip: {reason}")
            _set_cached(artist, title, None)  # Cache l'échec
            return None

        # Japonais : message informatif
        if case_analysis.get("is_japanese"):
            print(f"[LYRICS] [JP] Contenu japonais détecté - utilisation APIs standards")

        # Mashup auto-détecté : essayer de chercher les sources séparément
        if case_analysis.get("is_mashup"):
            sources = case_analysis.get("mashup_sources", [])
            if sources:
                print(f"[LYRICS] 🔀 Mashup détecté - {len(sources)} source(s) à chercher")
                # Pour l'instant, on normalise le titre pour chercher le morceau principal
                # TODO: implémenter la fusion de plusieurs sources de lyrics
    except ImportError:
        pass
    except Exception as e:
        print(f"[LYRICS] Erreur analyse cas particuliers: {e}")

    # ── Check mashup config (priorité pour les mashups manuels) ─────────────────
    try:
        import mashup_config
        mashup_result = mashup_config.lookup_mashup(artist, title)
        if mashup_result:
            synced, plain, offset = mashup_result
            if synced or plain:
                print(f"[LYRICS] [OK] mashup trouvé: {artist} - {title}")
                _set_cached(artist, title, {
                    "synced": synced or [],
                    "plain": plain,
                    "offset": offset,
                })
                return {
                    "synced": synced or [],
                    "plain": plain,
                    "offset": offset,
                }
    except ImportError:
        pass
    except Exception as e:
        print(f"[LYRICS] Erreur mashup config: {e}")

    # Check cache
    cached = _get_cached(artist, title)
    if cached is not None:
        return cached

    if cached is False:
        return None

    # Nettoie le titre - retire tout le junk à la fin
    import re as _re
    clean_title = title

    # PRIORITÉ: réparer les featuring mal formés (ex: "J ft. juicy" -> "ft. Juicy J")
    clean_title = _normalize_featuring(clean_title)

    # Retire [Official Video] [HD] (crochets)
    clean_title = _re.sub(r'\s*[\(\[][^\)\]]*[\)\]]\s*$', '', clean_title).strip()
    # Retire - Official - 1993, - Official Video, - HD etc (après un dash)
    clean_title = _re.sub(r'\s*[-–]\s*(Official|Video|HD|Remastered|Remaster|Explicit|Lyrics)\b.*$', '', clean_title, flags=_re.IGNORECASE).strip()
    # Retire l'année seule à la fin (ex: " - 1993")
    clean_title = _re.sub(r'\s*[-–]\s*\d{4}\s*$', '', clean_title).strip()
    # Retire les parenthèses contenant "Official", "Video", etc
    clean_title = _re.sub(r'\s*[\(\[][^()\]]*?(Official|Video|HD|Remaster|Version|Lyrics|Audio)[^)\]]*?[\)\]]', '', clean_title, flags=_re.IGNORECASE).strip()

    print(f"[LYRICS] recherche: {artist!r} / {clean_title!r}")

    # Stratégies de recherche (artist, title)
    searches = [
        (artist, clean_title),           # standard
        (artist, title),                 # original (au cas où)
    ]

    # Essaie aussi titre en minuscules pour certains APIs
    if clean_title != clean_title.lower():
        searches.append((artist, clean_title.lower()))

    for a, t in searches:
        print(f"[LYRICS]   -> essai: {a!r} / {t!r}")

        # Essai 1: Genius (plus fiable, bon pour rap/francais)
        try:
            data = _genius_fetch(a, t)
            if data:
                result = _result_to_lyrics(data)
                if result:
                    print(f"[LYRICS] OK Genius: {a!r} / {t!r}")
                    _set_cached(artist, title, result)
                    return result
        except Exception as e:
            print(f"[LYRICS]   Genius erreur: {e}")

        # Essai 2: lrclib.net (sync+plain) - timeout long pour synced lyrics
        try:
            data = _lrclib_get(a, t, timeout=15.0)  # 15s par requête
            if data:
                result = _result_to_lyrics(data)
                if result:
                    print(f"[LYRICS] OK lrclib: {a!r} / {t!r}")
                    _set_cached(artist, title, result)
                    return result
                else:
                    print(f"[LYRICS]   lrclib: data vide")
            else:
                print(f"[LYRICS]   lrclib: pas de réponse")
        except Exception as e:
            print(f"[LYRICS]   lrclib erreur: {e}")

        # Essai 3: lyrics.ovh (plain uniquement - dernier recours)
        try:
            data = _lyrics_ovh_get(a, t, timeout=2.0)  # 2s
            if data:
                result = _result_to_lyrics(data)
                if result:
                    print(f"[LYRICS] OK lyrics.ovh: {a!r} / {t!r}")
                    _set_cached(artist, title, result)
                    return result
        except Exception as e:
            print(f"[LYRICS]   lyrics.ovh erreur: {e}")

    # Cache l'échec
    _set_cached(artist, title, None)
    print(f"[LYRICS] [X] pas trouvé (essayé {len(searches)} variantes)")
    return None


def _current_idx(lines: list, pos: float) -> int:
    """Retourne l'index de la ligne active pour la position pos (secondes)."""
    if not lines:
        return 0
    idx = 0
    for i, (ts, _) in enumerate(lines):
        if ts is None:
            continue
        if ts <= pos:
            idx = i
        else:
            break
    return idx


def _has_sync(lyrics: dict | None) -> bool:
    """True si les paroles ont des timestamps (au moins la 1re ligne)."""
    return bool(lyrics and lyrics.get("synced") and lyrics["synced"][0][0] is not None)


def _load_offset(artist: str, title: str) -> tuple[float | None, bool]:
    """
    Retourne (offset, calibration_done) pour une chanson à partir du cache.

    Si la chanson a déjà un offset calibré (différent de la valeur par défaut),
    on l'applique immédiatement et on considère la calibration terminée.
    Sinon (None, False) -> l'auto-calibration démarrera.
    """
    cached = get_song_offset(artist, title)
    # 0.5 est la valeur par défaut posée à la création de l'entrée cache :
    # on ne la traite pas comme une vraie calibration.
    if cached is not None and abs(cached - 0.5) > 1e-6:
        # Validation : rejette les offsets absurdes (> 60s ou < -60s)
        if abs(cached) > 60:
            print(f"[LYRICS] [WARN] Offset absurde ignoré : {cached:+.2f}s (reset à 0)")
            set_song_offset(artist, title, 0.0)
            return None, False
        print(f"[LYRICS] [TARGET] offset chargé du cache : {cached:+.2f}s")
        return cached, True
    return None, False


def _create_estimated_sync(lines: list, bpm: float | None) -> list:
    """
    Crée des timestamps estimés pour des lyrics plain en utilisant le BPM.
    Estime ~4-5 beats par ligne de lyrics.
    """
    if not lines or bpm is None or bpm <= 0:
        return lines

    # Estime: ~4-5 beats par ligne en moyenne (dépend du tempo)
    beats_per_line = 4.5
    seconds_per_beat = 60.0 / bpm
    seconds_per_line = beats_per_line * seconds_per_beat

    synced_lines = []
    for i, (text) in enumerate(lines):
        timestamp = i * seconds_per_line
        synced_lines.append((timestamp, text))

    return synced_lines


def _load_lyrics_fast(artist: str, title: str, video_id: str | None) -> dict | None:
    """
    FAST MODE : Affiche des lyrics rapidement.
    1) Vérifie le cache d'abord
    2) Sinon, lrclib avec timeout court
    Retourne immédiatement un candidat utilisable ou None.
    """
    import re as _re

    # 1) Cache d'abord - instantané !
    key = _cache_key(artist, title)
    cache = _get_disk_cache()
    if key in cache:
        cached = cache[key]
        cached_lyrics = cached.get("lyrics")
        if cached_lyrics:
            print(f"[LYRICS] FAST Cache hit: {len(cached_lyrics)} lignes")
            return {
                "synced": cached_lyrics,
                "plain": cached.get("plain", ""),
                "source": "cache"
            }

    # 2) Sinon, lrclib avec timeout court
    clean_title = title
    clean_title = _normalize_featuring(clean_title)
    clean_title = _re.sub(r'\s*[\(\[][^\)\]]*[\)\]]\s*$', '', clean_title).strip()
    clean_title = _re.sub(r'\s*[-–]\s*(Official|Video|HD|Remastered|Remaster|Explicit|Lyrics)\b.*$', '', clean_title, flags=_re.IGNORECASE).strip()

    print(f"[LYRICS] FAST Cache miss - recherche lrclib (3s)")

    try:
        data = _lrclib_get(artist, clean_title, timeout=3.0)
        if data:
            result = _result_to_lyrics(data)
            if result and result.get("synced"):
                print(f"[LYRICS] FAST lrclib trouvé: {len(result['synced'])} lignes sync")
                result["source"] = "lrclib_fast"
                return result
    except Exception as e:
        print(f"[LYRICS] FAST lrclib error: {e}")

    return None


def _load_lyrics_for(artist: str, title: str, video_id: str | None, fast_first: bool = False) -> dict | None:
    """
    Charge les paroles en utilisant un SYSTÈME DE SCORING DE QUALITÉ.

    Modes:
      - fast_first=False (défaut): Fetch complet avec scoring
      - fast_first=True: Essaie lrclib rapide d'abord, puis continue en arrière-plan

    Pour une chanson donnée:
      1) Fetch les candidats disponibles (lrclib + YouTube captions)
      2) Scorers chacun selon: SYNC, MATCH MÉTADONNÉES, COHÉRENCE TEMPORELLE,
         QUALITÉ TEXTE
      3) Garder le meilleur score
      4) Genius reste fallback plain-only

    Le scoring permet de choisir la meilleure source même quand elle n'est pas
    la plus prioritaire (ex: lrclib bien syncé vs captions auto pourries).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    candidates = []
    media_duration = None  # Pourrait être enrichi via WinRT plus tard

    # Fast mode : retourne immédiatement si lrclib trouve
    if fast_first:
        fast_result = _load_lyrics_fast(artist, title, video_id)
        if fast_result:
            # Continue en arrière-plan pour trouver un meilleur candidat
            def _background_refine():
                try:
                    full_result = _load_lyrics_for(artist, title, video_id, fast_first=False)
                    if full_result:
                        # Comparer les scores
                        fast_score, _ = score_lyrics_candidate(fast_result, artist, title, media_duration, video_id)
                        full_score, _ = score_lyrics_candidate(full_result, artist, title, media_duration, video_id)
                        if full_score > fast_score * 1.2:  # 20% mieux
                            print(f"[LYRICS] [REFRESH] Upgrade: {fast_result['source']} -> {full_result['source']} (score {fast_score:.0f} -> {full_score:.0f})")
                            # Met à jour le cache
                            _set_cached(artist, title, full_result)
                            _flush_cache_if_dirty()
                except Exception as e:
                    print(f"[LYRICS] Background refine error: {e}")

            # Lance en arrière-plan sans bloquer
            import threading
            threading.Thread(target=_background_refine, daemon=True, name="lyrics_refine").start()

            return fast_result

    # ── Cas particuliers : détection automatique ────────────────────────────────
    try:
        import lyrics_special_cases as _lsc
        case_analysis = _lsc.analyze_track(artist, title)

        # Skip instrumental/DJ mix
        if case_analysis.get("should_skip"):
            reason = case_analysis.get("skip_reason", "")
            print(f"[LYRICS] [SKIP]  Skip: {reason}")
            return None

        # Japonais : message informatif
        if case_analysis.get("is_japanese"):
            print(f"[LYRICS] [JP] Contenu japonais détecté - utilisation APIs standards")
    except ImportError:
        pass
    except Exception as e:
        print(f"[LYRICS] Erreur analyse cas particuliers: {e}")

    # Nettoie le titre pour les recherches
    import re as _re
    clean_title = title
    clean_title = _normalize_featuring(clean_title)
    clean_title = _re.sub(r'\s*[\(\[][^\)\]]*[\)\]]\s*$', '', clean_title).strip()
    clean_title = _re.sub(r'\s*[-–]\s*(Official|Video|HD|Remastered|Remaster|Explicit|Lyrics)\b.*$', '', clean_title, flags=_re.IGNORECASE).strip()
    clean_title = _re.sub(r'\s*[-–]\s*\d{4}\s*$', '', clean_title).strip()
    clean_title = _re.sub(r'\s*[\(\[][^()\]]*?(Official|Video|HD|Remaster|Version|Lyrics|Audio)[^)\]]*?[\)\]]', '', clean_title, flags=_re.IGNORECASE).strip()

    # Normaliser les titres alternatifs (slowed + reverb, extended, etc.)
    try:
        import lyrics_special_cases as _lsc
        clean_title = _lsc.normalize_alternative_title(clean_title)
    except ImportError:
        pass

    print(f"[LYRICS] [SEARCH] Recherche multi-sources: {artist!r} / {clean_title!r}")

    # ── Fetch parallèle des candidats ───────────────────────────────────────────
    def _fetch_lrclib_candidate():
        try:
            data = _lrclib_get(artist, clean_title, timeout=15.0)
            if data:
                result = _result_to_lyrics(data)
                if result:
                    # Ajoute les métadonnées pour le scoring
                    result["artistName"] = data.get("artistName")
                    result["trackName"] = data.get("trackName")
                    result["duration"] = data.get("duration")
                    result["source"] = "lrclib"
                    return result
        except Exception as e:
            print(f"[LYRICS]   lrclib erreur: {e}")
        return None

    def _fetch_youtube_candidate():
        if video_id and not _quota_is_blocked():
            try:
                yt_lyrics = _fetch_youtube_captions(video_id)
                if yt_lyrics:
                    yt_lyrics["source"] = f"youtube_captions({video_id[:8]})"
                    return yt_lyrics
            except Exception as e:
                print(f"[LYRICS]   YouTube captions erreur: {e}")
        return None

    # Lance les fetchs en parallèle
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="lyrics_fetch") as executor:
        futures = {
            executor.submit(_fetch_lrclib_candidate): "lrclib",
            executor.submit(_fetch_youtube_candidate): "youtube",
        }

        try:
            for future in as_completed(futures, timeout=20):
                source_name = futures[future]
                try:
                    result = future.result()
                    if result:
                        candidates.append(result)
                        print(f"[LYRICS]   [V] {source_name}: {len(result.get('synced', []))} lignes sync")
                except Exception as e:
                    print(f"[LYRICS]   [X] {source_name} exception: {e}")
        except TimeoutError:
            # Timeout: on attend encore un peu pour les futures qui sont sur le point de finir
            pending = sum(1 for f in futures if not f.done())
            if pending:
                print(f"[LYRICS]   [TIME] Timeout après 20s, {pending} source(s) encore en cours - attente supplémentaire...")

            # Attend jusqu'à 5s de plus pour récupérer les résultats qui arrivent
            from concurrent.futures import wait
            finished, not_finished = wait(futures, timeout=5)

            for future in finished:
                source_name = futures[future]
                try:
                    result = future.result()
                    if result:
                        candidates.append(result)
                        print(f"[LYRICS]   [V] {source_name}: {len(result.get('synced', []))} lignes sync (après timeout)")
                except Exception as e:
                    print(f"[LYRICS]   [X] {source_name} exception (après timeout): {e}")
        except Exception as e:
            print(f"[LYRICS]   [WARN] Erreur inattendue pendant le fetch: {e}")

    # ── Scoring des candidats ───────────────────────────────────────────────────
    if not candidates:
        print(f"[LYRICS] [X] Aucun candidat trouvé")
        # Fallback Genius (plain-only)
        print(f"[LYRICS]   -> Fallback Genius (plain-only)...")
        try:
            data = _genius_fetch(artist, clean_title)
            if data:
                result = _result_to_lyrics(data)
                if result:
                    print(f"[LYRICS] OK Genius (fallback): {artist!r} / {clean_title!r}")
                    result["source"] = "genius_fallback"
                    _set_cached(artist, title, result)
                    return result
        except Exception as e:
            print(f"[LYRICS]   Genius erreur: {e}")
        return None

    # Score chaque candidat
    scored_candidates = []
    for i, cand in enumerate(candidates):
        score, details = score_lyrics_candidate(
            cand, artist, title, media_duration, video_id
        )
        scored_candidates.append((score, details, cand))
        source = cand.get("source", f"candidate_{i}")
        reasons = "; ".join(details["reason"])
        print(f"[LYRICS]   [STATS] {source}: score={score:.0f} [{reasons}]")

    # Cross-validation si plusieurs candidats sync
    if len(scored_candidates) >= 2:
        synced_candidates = [c for _, _, c in scored_candidates if c.get("synced") and c["synced"][0][0] is not None]
        if len(synced_candidates) >= 2:
            comparisons = _compare_candidates_cross(synced_candidates, artist, title)
            if comparisons:
                print(f"[LYRICS]   [LINK] Cross-validation: {len(comparisons)} paires similaires")

    # Choix du meilleur
    best_score, best_details, best_candidate = max(scored_candidates, key=lambda x: x[0])
    best_source = best_candidate.get("source", "unknown")

    print(f"[LYRICS] [OK] Meilleur candidat: {best_source} (score={best_score:.0f})")

    # Sauvegarde dans le cache avec métadonnées
    existing = _get_disk_cache().get(_cache_key(artist, title), {})
    _set_cached_lyrics(
        artist, title,
        best_candidate.get("synced", []),
        best_candidate.get("plain", ""),
        existing.get("offset", 0.5),
    )
    _flush_cache_if_dirty()

    return best_candidate


# ── Thread principal ──────────────────────────────────────────────────────────

def _run():
    global _state, _running, _beat_counter, _last_beat_ts, _last_beat_seq, _song_start_beats, _last_sync_pos
    global _youtube_pos, _youtube_pos_t, _youtube_artist, _youtube_title, _youtube_video_id
    global _calibration_active, _calibration_samples, _calibrated_offset, _calibration_done
    global _first_listen_mode, _next_sample_pos, _sample_interval
    global _kf_state, _kf_covariance, _kf_process_noise, _kf_measurement_noise, _calibration_stats
    global _current_search_key, _youtube_captions_tried
    global _youtube_last_video_id, _youtube_video_ids_seen

    last_key       = None
    lyrics         = None
    artist         = ""
    title          = ""
    fail_count     = 0
    FETCH_INTERVAL = 2.0
    FAIL_CLEAR     = 15  # 15 échecs = 30 secondes avant de désactiver (était 5)
    last_fetch_t   = -FETCH_INTERVAL

    # ── Position tracking ─────────────────────────────────────────────────
    smtc_pos        = None    # dernière position SMTC reçue (secondes)
    smtc_pos_t      = None    # monotonic au moment où smtc_pos a été lu
    song_start_t    = None    # monotonic quand la chanson a été détectée
    pos_frozen_count = 0      # combien de fois SMTC a renvoyé la même valeur

    def _raw_pos() -> float | None:
        global _beat_counter, _last_sync_pos, _youtube_pos, _youtube_pos_t
        """
        Estime la position de lecture brute avec beat-sync.
        Priorité: YouTube extension > beats > SMTC > timer local
        """
        # ── YouTube extension (priorité absolue) ───────────────────────────────────
        if _youtube_pos is not None and _youtube_pos_t is not None:
            elapsed = time.monotonic() - _youtube_pos_t
            yt_current = _youtube_pos + elapsed
            # Utiliser YouTube comme référence pour resync les beats
            if _beat_counter >= 4:
                try:
                    import bpm_source as _bs
                    bpm = _bs.get_bpm()
                    if bpm and bpm > 0:
                        # Resync beat_counter sur YouTube position
                        expected_beats = int(yt_current * bpm / 60)
                        if abs(expected_beats - _beat_counter) > 2:  # désync de +2 beats
                            _beat_counter = expected_beats
                            _last_sync_pos = yt_current
                except Exception:
                    pass
            return yt_current

        # ── Try beat-locked position ───────────────────────────────────────────
        if _beat_counter >= 4:  # besoin d'au moins 4 beats pour estimer
            try:
                import bpm_source as _bs
                bpm = _bs.get_bpm()
                if bpm and bpm > 0:
                    # Position basée sur le nombre de beats
                    # beats / BPM = minutes écoulées
                    beats_since_start = _beat_counter
                    # Assume 4/4 time signature (standard)
                    # Chaque beat = 60/BPM secondes
                    pos_from_beats = (beats_since_start * 60) / bpm

                    # Calibre avec SMTC si dispo (pour le début de chanson)
                    if smtc_pos is not None and smtc_pos > 1.0:
                        # SMTC nous donne la position absolue
                        # On l'utilise pour calibrer le "zéro" des beats
                        # Pour l'instant, simple : SMTC + interpolation
                        elapsed = time.monotonic() - smtc_pos_t
                        smtc_current = smtc_pos + elapsed

                        # Si l'écart est trop grand (>3s), resync sur SMTC
                        if _last_sync_pos is None or abs(smtc_current - pos_from_beats) > 3.0:
                            # Resync : on ajuste le beat_counter pour matcher SMTC
                            # beat_counter = pos * BPM / 60
                            _beat_counter = int(smtc_current * bpm / 60)
                            _last_sync_pos = smtc_current
                            print(f"[LYRICS] beat-sync resync: {smtc_current:.1f}s -> {_beat_counter} beats")
                            return smtc_current

                    # Log activé beat-sync
                    if int(now) % 30 == 0:
                        print(f"[LYRICS] beat-sync actif: {pos_from_beats:.1f}s ({_beat_counter} beats @ {bpm:.0f}BPM)")

                    return pos_from_beats
            except Exception:
                pass

        # ── Fallback SMTC ─────────────────────────────────────────────────────
        if smtc_pos is not None and smtc_pos > 1.0 and smtc_pos_t is not None:
            # SMTC donne une vraie position -> l'interpoler
            elapsed = time.monotonic() - smtc_pos_t
            return smtc_pos + elapsed

        # ── Fallback timer local ──────────────────────────────────────────────
        if song_start_t is not None:
            # Timer local depuis que la chanson a été détectée
            return time.monotonic() - song_start_t

        return None

    def _effective_pos() -> float | None:
        """
        Position effective = position brute corrigée par l'offset calibré.

        sample = raw_pos - lyric_ts  (positif = lecture en avance sur la parole)
        donc la position synchronisée sur les paroles = raw_pos - offset.
        L'offset vient de l'auto-calibration ou du cache de la chanson.
        """
        raw = _raw_pos()
        if raw is None:
            return None
        offset = _calibrated_offset
        if offset is None:
            return raw
        return raw - offset

    while _running:
        try:
            now = time.monotonic()
    
            if now - last_fetch_t >= FETCH_INTERVAL:
                last_fetch_t = now
    
                # ── YouTube extension (priorité absolue) ────────────────────────────────
                if _youtube_artist and _youtube_title:
                    global _youtube_captions_tried  # Déclarer global ici pour toute la section
    
                    # Clé UNIQUE basée sur video_id pour détecter tout changement de vidéo
                    # (video_id change TOUJOURS la clé, même si artiste/titre identiques)
                    yt_key = f"yt:{_youtube_video_id or '?'}|{_youtube_artist.lower()}|{_youtube_title.lower()}"
                    # Re-fetch si nouvelle vidéo (video_id changé) OU si on a pas encore essayé les captions
                    need_refetch = (yt_key != last_key) or (
                        _youtube_video_id and not _youtube_captions_tried and not _has_sync(lyrics)
                    )

                    # Détecte si le nom de la chaîne est suspect (playlist/remixer)
                    # Patterns: (mimijil), (DJ X), etc.
                    import re as _re
                    suspicious_patterns = [
                        r'\([^)]*mimi[^)]*\)',
                        r'\([^)]*dj\s*\w*[^)]*\)',
                        r'\([^)]*mix[^)]*\)',
                        r'\([^)]*edit[^)]*\)',
                    ]
                    is_suspicious_channel = any(_re.search(p, _youtube_artist, _re.IGNORECASE) for p in suspicious_patterns)

                    # SAUF si le nom de chaîne est suspect ET qu'on a déjà des lyrics SMTC
                    if is_suspicious_channel and lyrics and _has_sync(lyrics):
                        # On a déjà des bons lyrics via SMTC -> ignorer YouTube
                        need_refetch = False
                        if int(now) % 30 == 0:  # Log toutes les 30s pour éviter spam
                            print(f"[LYRICS] [SKIP]  Skip YouTube (chaîne suspecte: {_youtube_artist}) - utilise SMTC")

                    # Si on a déjà des lyrics pour CETTE chanson (même artiste/titre), pas besoin de refaire
                    if lyrics and artist.lower() == _remove_topic_noise(_youtube_artist).lower() and title.lower() in _youtube_title.lower():
                        need_refetch = False
                        if int(now) % 30 == 0:
                            print(f"[LYRICS] [SKIP]  Lyrics déjà chargés pour cette chanson")

                    if need_refetch:
                        # Annule toute recherche en cours
                        global _current_search_key
                        _current_search_key = None  # <- Annule la recherche précédente
                        _youtube_captions_tried = False  # Reset pour nouvelle vidéo
    
                        # Nouvelle vidéo YouTube -> fetch lyrics
                        # Nettoie les noms YouTube (retire " - Topic - " etc.)
                        clean_artist = _remove_topic_noise(_youtube_artist)
                        print(f"[LYRICS] YouTube: {clean_artist} - {_youtube_title}")
                        artist, title = _clean_artist(clean_artist), _youtube_title
                        key = yt_key
                        last_key = key
                        fail_count = 0
                        song_start_t = None  # YouTube gère sa propre position
    
                        # AFFICHAGE IMMÉDIAT : Recherche en cours...
                        with _lock:
                            _state = {
                                "artist": artist,
                                "title": title,
                                "lines": [],
                                "current_idx": 0,
                                "position": None,
                                "has_sync": False,
                                "loading": True,  # <- État de chargement
                            }
    
                        # Première écoute jamais ? (vérifier avant _fetch qui ajoute au cache)
                        _first_listen_mode = _cache_key(artist, title) not in _get_disk_cache()
    
                        # Fast mode: lrclib rapide d'abord, puis smart refinement
                        lyrics = _load_lyrics_for(artist, title, _youtube_video_id, fast_first=True)
    
                        # Marquer qu'on a essayé les captions pour cette vidéo
                        _youtube_captions_tried = True
    
                        if lyrics:
                            print(f"[LYRICS] {len(lyrics['synced'])} lignes")
                        else:
                            print("[LYRICS] introuvable")
    
                        if _first_listen_mode and lyrics:
                            print(f"[LYRICS] [AUDIO] première écoute - calibration active avec Kalman filter")
    
                        # Reset beat tracking + calibration
                        _beat_counter = 0
                        _last_beat_seq = None
                        _song_start_beats = None
                        _last_sync_pos = None
                        _calibration_active = False
                        _calibration_samples = []
                        _calibrated_offset, _calibration_done = _load_offset(artist, title)
                        _next_sample_pos = 5.0  # Premier sample à 5s
                        _kalman_init()  # Reset Kalman filter
                    # Même vidéo, rien à faire
                else:
                    # ── Pas de YouTube -> essai BPM/SMTC ────────────────────────────────────
                    r_from_bpm = None
                    try:
                        import bpm_source as _bs
                        bpm_artist, bpm_title = None, None
                        if hasattr(_bs, '_get_now_playing'):
                            result = _bs._get_now_playing()
                            if result:
                                bpm_artist, bpm_title = result
                                # Nettoie le nom : retire " - Topic - " et autres bruits
                                if bpm_artist and " - Topic - " in bpm_artist:
                                    bpm_artist = bpm_artist.replace(" - Topic - ", " ")
                        if bpm_artist and bpm_title:
                            bpm_artist = _remove_topic_noise(bpm_artist)
                            r_from_bpm = (bpm_artist, bpm_title, None)
                    except Exception:
                        pass
    
                    r = r_from_bpm or _winrt_fetch()
    
                    if r is None:
                        fail_count += 1
                        if fail_count == 1:
                            print("[LYRICS] aucune session média détectée")
                        elif fail_count % 5 == 0:
                            print(f"[LYRICS] toujours rien ({fail_count}/{FAIL_CLEAR})")
                        if fail_count >= FAIL_CLEAR:
                            with _lock:
                                _state = None
                            last_key = None
                            lyrics = None
                            song_start_t = None
                    else:
                        a, t, p = r
                        fail_count = 0
    
                        if p is not None and p > 0:
                            if smtc_pos is not None and abs(p - smtc_pos) < 0.1:
                                pos_frozen_count += 1
                            else:
                                pos_frozen_count = 0
                                smtc_pos = p
                                smtc_pos_t = now
    
                        if not t:
                            continue
                        if " - " in t and not a:
                            parts = [x.strip() for x in t.split(" - ", 1)]
                            a, t = parts[0], parts[1]
                        artist, title = _clean_artist(a), t
                        key = f"{artist.lower()}|{title.lower()}"
                        if key != last_key:
                            last_key = key
                            lyrics = None
                            song_start_t = now
                            smtc_pos = p if (p and p > 0) else None
                            smtc_pos_t = now if smtc_pos else None
                            pos_frozen_count = 0
                            print(f"[LYRICS] ~ {artist} - {title}  [SMTC pos={p}]")
    
                            # RESET calibration quand la chanson change
                            _calibration_active = False
                            _calibration_samples = []
                            _calibration_done = False
                            _calibrated_offset = None
                            _next_sample_pos = 5.0
                            _first_listen_mode = True
                            _kalman_init()
    
                            # Annule toute recherche en cours
                            _current_search_key = None  # <- Annule la recherche précédente
    
                            # AFFICHAGE IMMÉDIAT : Recherche en cours...
                            with _lock:
                                _state = {
                                    "artist": artist,
                                    "title": title,
                                    "lines": [],
                                    "current_idx": 0,
                                    "position": None,
                                    "has_sync": False,
                                    "loading": True,  # <- État de chargement
                                }
    
                            # Première écoute jamais ? (vérifier avant _fetch_lyrics qui ajoute au cache)
                            _first_listen_mode = _cache_key(artist, title) not in _get_disk_cache()
    
                            # Si l'extension YouTube a un video_id pour CETTE chanson, on
                            # tente d'abord les captions YouTube (synchro) même en SMTC.
                            vid = None
                            if (_youtube_video_id and _youtube_title
                                    and _youtube_title.lower() in title.lower()):
                                vid = _youtube_video_id
                            lyrics = _load_lyrics_for(artist, title, vid, fast_first=True)
                            if lyrics:
                                print(f"[LYRICS] {len(lyrics['synced'])} lignes")
                            else:
                                print("[LYRICS] introuvable")
    
                            if _first_listen_mode and lyrics:
                                print(f"[LYRICS] [AUDIO] première écoute - calibration active avec Kalman filter")
    
                            # Reset beat tracking + calibration
                            _beat_counter = 0
                            _last_beat_seq = None
                            _song_start_beats = None
                            _last_sync_pos = None
                            _calibration_active = False
                            _calibration_samples = []
                            _calibrated_offset, _calibration_done = _load_offset(artist, title)
                            _next_sample_pos = 5.0  # Premier sample à 5s
                            _kalman_init()  # Reset Kalman filter
                        else:
                            if int(now) % 10 == 0:
                                eff = _effective_pos()
                                print(f"[LYRICS pos] SMTC={p}  eff={eff:.1f}s" if eff else f"[LYRICS pos] SMTC={p}  eff=None")
    
            # ── Beat tracking depuis bpm_source ─────────────────────────────────────
            try:
                import bpm_source as _bs
                beat_ts, beat_seq = _bs.get_beat_event()
                if beat_seq is not None and beat_seq != _last_beat_seq:
                    # Nouveau beat détecté !
                    _last_beat_seq = beat_seq
                    _beat_counter += 1
                    _last_beat_ts = beat_ts
    
                    # Initialisation : premier beat de la chanson
                    if _song_start_beats is None:
                        _song_start_beats = beat_seq
                        _beat_counter = 0
    
                    # ── Auto-calibration offset avec Kalman filter ────────────────────────
                    # NE lance l'auto-calibration QUE si: first listen mode ET pas déjà calibré
                    if (_first_listen_mode and not _calibration_done and lyrics and lyrics["synced"]
                            and lyrics["synced"][0][0] is not None):
                        # On mesure sur la position BRUTE (sans offset déjà appliqué)
                        raw_pos = _raw_pos()
                        if raw_pos is not None and raw_pos >= _next_sample_pos:
                            try:
                                idx = _current_idx(lyrics["synced"], raw_pos)
                                # Arrêter la calibration si on est à la fin des lyrics
                                # (sinon l'offset mesuré drift car current_lyric_ts ne bouge plus)
                                if idx >= len(lyrics["synced"]) - 2:
                                    _calibration_done = True
                                current_lyric_ts = lyrics["synced"][idx][0]
                                if current_lyric_ts is not None:
                                    sample = raw_pos - current_lyric_ts
                                    sample_ts = time.monotonic()
                                    _calibration_samples.append((sample_ts, sample))
                                    _calibration_active = True
                                    _next_sample_pos += _sample_interval
                                    n = len(_calibration_samples)
    
                                    # Mettre à jour le Kalman filter
                                    kalman_est = _kalman_update(sample)
                                    _calibrated_offset = kalman_est
    
                                    # Éliminer les outliers une seule fois (après 10 samples)
                                    # Pas de filtrage cyclique pour éviter l'effet boule de neige
                                    if n == 10 and not _calibration_done:
                                        filtered = _remove_outliers_iqr(_calibration_samples)
                                        if len(filtered) < len(_calibration_samples):
                                            print(f"[LYRICS] 🧹 {len(_calibration_samples) - len(filtered)} outlier(s) éliminé(s) - IQR filtering unique")
                                            # Reset Kalman avec les données propres
                                            _kalman_init()
                                            for ts, off in filtered:
                                                _kalman_update(off)
                                            _calibration_samples = filtered
                                            kalman_est = _kf_state  # Recalculer après reset
                                            n = len(_calibration_samples)  # Recalculer n après filtrage
    
                                    # Mettre à jour les stats
                                    _update_calibration_stats(_calibration_samples)
    
                                    print(f"[LYRICS] 📐 sample #{n} @{raw_pos:.0f}s -> offset {sample:+.2f}s | KF: {kalman_est:+.2f}s | σ: {_calibration_stats['std']:.2f}s | conf: {_calibration_stats['confidence']:.0%}")

                                    # Mise à jour du cache SEULEMENT si pas d'offset manuel existant
                                    # Protection des offsets définis manuellement par l'utilisateur
                                    cache = _get_disk_cache()
                                    ck = _cache_key(artist, title)
                                    if ck in cache:
                                        current_offset = cache[ck].get("offset", 0.5)
                                        # Ne pas écraser si l'offset est significativement différent de 0.5 (indique un réglage manuel)
                                        is_manual_offset = abs(current_offset - 0.5) > 0.1
                                        if not is_manual_offset:
                                            cache[ck]["offset"] = kalman_est
                                            _mark_cache_dirty()
                                            _flush_cache_if_dirty()
                                        else:
                                            print(f"[LYRICS] 🔒 Offset manuel protégé: {current_offset:+.2f}s (auto-calibration ignorée)")
    
                                    # Arrêt quand confiance > 90% et std < 0.3s
                                    # OU après 50 samples (timeout pour éviter boucle infinie)
                                    if ((_calibration_stats["confidence"] >= 0.9
                                            and _calibration_stats["std"] is not None
                                            and _calibration_stats["std"] < 0.3
                                            and n >= 8)
                                        or n >= 50):
                                        if n >= 50:
                                            print(f"[LYRICS] [WARN] calibration TEMPOUT (50 samples) - offset final : {_calibrated_offset:+.2f}s (σ={_calibration_stats['std']:.2f}s)")
                                        else:
                                            print(f"[LYRICS] [OK] calibration HAUTE PRÉCISION - offset final : {_calibrated_offset:+.2f}s (σ={_calibration_stats['std']:.2f}s, {_calibration_stats['outliers_removed']} outliers)")
                                        _calibration_done = True
                            except Exception:
                                pass
            except Exception:
                pass  # bpm_source pas dispo
    
            # ── Mise à jour state ──────────────────────────────────────────────
            if last_key is not None:
                eff_pos = _effective_pos()
                with _lock:
                    if lyrics and lyrics["synced"]:
                        idx = _current_idx(lyrics["synced"], eff_pos) \
                              if eff_pos is not None and lyrics["synced"][0][0] is not None else 0
                        _state = {
                            "artist":      artist,
                            "title":       title,
                            "lines":       lyrics["synced"],
                            "current_idx": idx,
                            "position":    eff_pos,
                            "has_sync":    lyrics["synced"][0][0] is not None,
                            "loading":     False,  # <- Chargement terminé
                        }
                    else:
                        _state = {
                            "artist": artist, "title": title,
                            "lines": [], "current_idx": 0, "position": None, "has_sync": False,
                            "loading": False,  # <- Pas de lyrics trouvées
                        }
    
            # ── Cadence de la boucle ───────────────────────────────────────────
            # Tour rapide pour un suivi fluide de la position; le fetch reste
            # limité à FETCH_INTERVAL via le garde-fou en tête de boucle.
            time.sleep(0.05)
        except Exception as e:
            # Protection: empêche le thread de crasher définitivement
            import traceback
            print(f"[LYRICS] [WARN] Erreur dans la boucle principale: {e}")
            print(f"[LYRICS] Stack trace: {traceback.format_exc()[-500:]}")  # Derniers 500 chars
            time.sleep(1.0)


# ── API publique ────────────────────────────────────────────────────────────────
def start():
    """Démarre la boucle de fetch des paroles dans un thread daemon.

    Idempotent : un second appel ne lance pas de thread supplémentaire.
    """
    global _running, _thread
    if _running and _thread is not None and _thread.is_alive():
        return
    _running = True
    _thread = threading.Thread(target=_run, name="lyrics_source", daemon=True)
    _thread.start()


def stop():
    """Arrête la boucle de fetch, flush le cache et attend la fin du thread."""
    global _running, _thread
    _running = False
    # Flush le cache pour sauvegarder la calibration en cours
    _flush_cache_if_dirty()
    if _thread is not None:
        _thread.join(timeout=2.0)
        _thread = None


def get_state() -> dict | None:
    """Retourne l'état courant des paroles (copie), ou None si rien en lecture."""
    with _lock:
        if _state is None:
            return None
        return dict(_state)


def resync():
    """Force un re-fetch des paroles + recalibration (Ctrl+Shift+L).

    Réinitialise les clés de recherche et l'état de calibration pour que la
    boucle _run relance un fetch complet au prochain tour.
    """
    global _current_search_key, _youtube_captions_tried
    global _calibration_active, _calibration_samples, _calibration_done
    global _calibrated_offset, _next_sample_pos, _first_listen_mode
    with _lock:
        _current_search_key = None
        _youtube_captions_tried = False
        _calibration_active = False
        _calibration_samples = []
        _calibration_done = False
        _calibrated_offset = None
        _next_sample_pos = 5.0
        _first_listen_mode = True
    _kalman_init()
    print("[LYRICS] [REFRESH] resync demandé - recherche + recalibration relancées")
