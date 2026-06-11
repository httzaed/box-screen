"""
auto_mode.py — Mode auto-intelligent pour Box Screen

Système de scoring avec hystérésis pour éviter le flapping.
Chaque mode reçoit un score basé sur le contexte enrichi.

Architecture:
  - Contexte enrichi: audio (EMA, BPM stability), lyrics (synced vs plain),
    vidéo (duration + playing), heure (nuit/jour)
  - Scoring: chaque mode reçoit un score 0-100
  - Hystérésis: ne change que si nouveau score > score actuel + marge (20%)
  - Transitions intelligentes: audio_viz en attente entre morceaux
  - Override manuel: pause X minutes sur détection de mode manuel
  - Apprentissage léger: logging des décisions + ajustement des poids
"""

import pathlib
import time
import threading
import json
import math
from collections import deque
from datetime import datetime as dt

_HERE = pathlib.Path(__file__).parent
_STATE_FILE = _HERE / "last_state.json"
_LOG_FILE = _HERE / "auto_mode_log.jsonl"
_LEARNING_FILE = _HERE / "auto_mode_learning.json"

# ── Configuration ──────────────────────────────────────────────────────────────

# Modes disponibles (doit matcher MODES dans launcher.py)
_MODES = ["ascii_vhs", "video", "image", "audio", "blank"]
_HUD_STYLES = ["full", "terminal", "clock", "split", "tiles", "matrix", "lyrics", "audio_viz", "blank"]

# Combinaisons mode+hud valides
_MODE_COMBINATIONS = {
    "video+lyrics": ("video", "lyrics"),
    "video": ("video", "full"),
    "image+lyrics": ("image", "lyrics"),
    "image": ("image", "full"),
    "lyrics": ("blank", "lyrics"),
    "audio_viz": ("blank", "audio_viz"),
    "ascii_hud": ("ascii_vhs", "terminal"),
    "blank": ("blank", "blank"),
}

# LED configs par combinaison
_LED_CONFIGS = {
    "video+lyrics": {"case": "static", "fans": "beat_pulse"},
    "video": {"case": "static", "fans": "beat_pulse"},
    "image+lyrics": {"case": "static", "fans": "beat_pulse"},
    "image": {"case": "static", "fans": "beat"},
    "lyrics": {"case": "static", "fans": "beat_pulse"},
    "audio_viz": {"case": "static", "fans": "beat_pulse"},
    "ascii_hud": {"case": "beat", "fans": "spin"},
    "blank": {"case": "off", "fans": "off"},
}

# Hystérésis
_HYSTERESIS_MARGIN = 20.0  # % de marge
_HYSTERESIS_MIN_TIME = 10.0  # secondes minimum avant changement
_SILENCE_THRESHOLD = 8.0  # secondes de silence avant bascule (anti-flapping entre morceaux)

# Override manuel
_OVERRIDE_PAUSE_DURATION = 5.0  # minutes de pause après override manuel

# Audio EMA (niveau moyen lissé)
_AUDIO_EMA_ALPHA = 0.15  # coefficient pour EMA (~5s de mémoire)
_AUDIO_EMA_WINDOW = int(5.0 / 0.15)  # nombre d'échantillons pour ~5s

# Heures "nuit" (pénalité pour modes lumineux)
_NIGHT_START = 1  # 1h du matin
_NIGHT_END = 9   # 9h du matin

# ── État global ────────────────────────────────────────────────────────────────

_lock = threading.Lock()

# État de l'auto-mode
_state = {
    "current_mode": None,  # Mode actuel (clé de _MODE_COMBINATIONS)
    "current_score": 0.0,
    "scores": {},  # Scores de chaque mode pour debug
    "last_change_time": 0.0,
    "override_until": 0.0,  # timestamp jusqu'auquel l'auto est en pause
    "context": None,  # Dernier contexte détecté
    "learning_weights": {},  # Poids ajustés par apprentissage
}

# Audio EMA state
_audio_ema = deque(maxlen=_AUDIO_EMA_WINDOW)
_audio_ema_value = 0.0

# BPM stability tracking
_bpm_history = deque(maxlen=10)  # Garder les 10 derniers BPM
_bpm_stability = 1.0  # 1.0 = stable, 0.0 = très instable

# Silence detection
_last_music_time = 0.0
_silence_detected = False

# Transitions intelligentes
_transition_to_idle = False  # True si on est en transition vers idle
_last_music_mode = None  # Dernier mode musical (lyrics/audio_viz)

# ── Contexte enrichi ─────────────────────────────────────────────────────────────

def get_video_duration(video_path):
    """Retourne durée en secondes, ou None si impossible."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()

        if fps and fps > 0 and frame_count:
            duration = frame_count / fps
            return duration
    except Exception:
        pass
    return None


def _is_video_playing(video_path):
    """Détecte si une vidéo est en cours de lecture (position qui avance)."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return False

        # Lire deux frames avec délai pour vérifier que la position avance
        pos1 = cap.get(cv2.CAP_PROP_POS_MSEC)
        time.sleep(0.2)
        ret, _ = cap.read()
        pos2 = cap.get(cv2.CAP_PROP_POS_MSEC)
        cap.release()

        # Si la position avance de plus de 100ms, la vidéo joue
        return ret and (pos2 - pos1) > 100
    except Exception:
        return False


def _is_night_hour():
    """Retourne True si on est dans la plage horaire 'nuit'."""
    hour = dt.now().hour
    return _NIGHT_START <= hour < _NIGHT_END


def detect_context():
    """
    Analyse le contexte actuel et retourne un dict enrichi.

    Returns:
        dict: {
            "has_video": bool,
            "video_duration": float|None,
            "video_playing": bool,
            "has_music": bool,
            "music_level": float,  # 0-1
            "music_level_ema": float,  # Niveau moyen lissé
            "bpm": float|None,
            "bpm_stable": bool,
            "silence_prolonged": bool,  # True si silence depuis > _SILENCE_THRESHOLD
            "has_lyrics": bool,
            "lyrics_synced": bool,  # True si lyrics synchronisés (lrclib)
            "is_night": bool,
        }
    """
    global _audio_ema_value, _bpm_stability, _last_music_time, _silence_detected

    context = {
        "has_video": False,
        "video_duration": None,
        "video_playing": False,
        "has_music": False,
        "music_level": 0.0,
        "music_level_ema": 0.0,
        "bpm": None,
        "bpm_stable": False,
        "silence_prolonged": False,
        "has_lyrics": False,
        "lyrics_synced": False,
        "is_night": _is_night_hour(),
    }

    # Détection vidéo
    video_files = list(_HERE.glob("*.mp4"))
    if video_files:
        context["has_video"] = True
        duration = get_video_duration(video_files[0])
        context["video_duration"] = duration
        context["video_playing"] = _is_video_playing(video_files[0])

    # Détection musique (BPM OU niveau audio élevé)
    bpm = None
    level = 0.0
    try:
        import bpm_source
        bpm = bpm_source.get_bpm()
        level = bpm_source.get_level()

        # EMA du niveau audio
        _audio_ema.append(level)
        if _audio_ema:
            _audio_ema_value = sum(_audio_ema) / len(_audio_ema)

        # Considère musique si BPM détecté OU niveau audio au-dessus du seuil
        context["has_music"] = (bpm is not None) or (level > 0.03)
        context["music_level"] = level
        context["music_level_ema"] = _audio_ema_value

        # Stabilité BPM
        if bpm:
            _bpm_history.append(bpm)
            if len(_bpm_history) >= 3:
                # Coefficient de variation (écart-type / moyenne)
                mean_bpm = sum(_bpm_history) / len(_bpm_history)
                variance = sum((x - mean_bpm) ** 2 for x in _bpm_history) / len(_bpm_history)
                cv = math.sqrt(variance) / mean_bpm if mean_bpm > 0 else 1.0
                # CV < 0.05 = stable, CV > 0.15 = instable
                _bpm_stability = max(0.0, min(1.0, 1.0 - (cv - 0.05) / 0.1))
                context["bpm_stable"] = _bpm_stability > 0.7

        # Silence prolongé
        now = time.monotonic()
        if context["has_music"]:
            _last_music_time = now
        elif now - _last_music_time > _SILENCE_THRESHOLD:
            context["silence_prolonged"] = True

        context["bpm"] = bpm
    except Exception:
        pass

    # Détection lyrics
    try:
        import lyrics_source
        lyrics_state = lyrics_source.get_state()
        if lyrics_state:
            lines = lyrics_state.get("lines", [])
            # Vérifie qu'il y a des lignes avec du texte réel
            has_real_lyrics = any(
                line[1].strip() if len(line) > 1 else False
                for line in lines if line
            )
            context["has_lyrics"] = has_real_lyrics
            # Distingue synced (lrclib) vs plain
            context["lyrics_synced"] = lyrics_state.get("has_sync", False) and has_real_lyrics
    except Exception:
        pass

    return context


# ── Scoring ────────────────────────────────────────────────────────────────────

def _base_scores(context, learning_weights):
    """
    Calcule les scores de base pour chaque mode selon le contexte.

    Returns:
        dict: {mode_key: score_0_100}
    """
    scores = {k: 0.0 for k in _MODE_COMBINATIONS.keys()}

    # Poids ajustables par apprentissage (défaut: 1.0)
    w_video = learning_weights.get("w_video", 1.0)
    w_music = learning_weights.get("w_music", 1.0)
    w_lyrics_synced = learning_weights.get("w_lyrics_synced", 1.0)
    w_lyrics_plain = learning_weights.get("w_lyrics_plain", 1.0)
    w_night = learning_weights.get("w_night", 1.0)

    has_video = context.get("has_video", False)
    has_music = context.get("has_music", False)
    has_lyrics = context.get("has_lyrics", False)
    lyrics_synced = context.get("lyrics_synced", False)
    is_night = context.get("is_night", False)

    # ── VIDEO+LYRICS ─────────────────────────────────────────────────────────────
    if has_video and has_lyrics:
        base = 85.0
        if lyrics_synced:
            base += 15.0  # Gros bonus pour lyrics syncés
        if not context.get("video_playing", True):
            base -= 20.0  # Pénalité si vidéo ne joue pas
        scores["video+lyrics"] = base * w_video * w_lyrics_synced if lyrics_synced else base * w_video * w_lyrics_plain

    # ── VIDEO ─────────────────────────────────────────────────────────────────────
    if has_video:
        base = 60.0
        if not context.get("video_playing", True):
            base -= 30.0  # Forte pénalité si vidéo ne joue pas
        if is_night:
            base -= 15.0 * w_night  # Pénalité nocturne
        scores["video"] = base * w_video

    # ── LYRICS ───────────────────────────────────────────────────────────────────
    if has_music and has_lyrics:
        base = 70.0
        if lyrics_synced:
            base += 20.0  # Gros bonus pour synced
        else:
            base -= 10.0  # Pénalité pour plain lyrics
        scores["lyrics"] = base * w_music * (w_lyrics_synced if lyrics_synced else w_lyrics_plain)

    # ── AUDIO_VIZ ─────────────────────────────────────────────────────────────────
    if has_music:
        base = 50.0
        # Bonus si BPM stable (meilleur viz)
        if context.get("bpm_stable", False):
            base += 15.0
        # Bonus si niveau audio élevé
        if context.get("music_level_ema", 0) > 0.1:
            base += 10.0
        scores["audio_viz"] = base * w_music

    # ── ASCII_HUD (mode idle par défaut) ──────────────────────────────────────────
    # Score de base pour ascii_hud
    base_ascii = 30.0

    # Bonus la nuit (mode discret)
    if is_night:
        base_ascii += 20.0 * w_night

    # Bonus si silence prolongé (mode d'attente)
    if context.get("silence_prolonged", False):
        base_ascii += 15.0

    # Si aucune activité, ascii_hud gagne
    if not has_video and not has_music:
        base_ascii = 80.0

    scores["ascii_hud"] = base_ascii

    # ── IMAGE (rare, fallback) ───────────────────────────────────────────────────
    if has_video:  # Si image disponible mais pas de priorité
        scores["image"] = 20.0 * w_video
    if has_music and has_lyrics and not lyrics_synced:
        scores["image+lyrics"] = 25.0 * w_music * w_lyrics_plain

    # ── BLANK (minimum) ───────────────────────────────────────────────────────────
    scores["blank"] = 10.0

    return scores


# ── Décision avec hystérésis ─────────────────────────────────────────────────────

def _should_change_mode(current_mode, current_score, new_mode, new_score, time_since_change):
    """
    Décide si on doit changer de mode en appliquant l'hystérésis.

    Args:
        current_mode: Mode actuel
        current_score: Score du mode actuel
        new_mode: Mode candidat
        new_score: Score du mode candidat
        time_since_change: Temps écoulé depuis le dernier changement

    Returns:
        bool: True si le changement est autorisé
    """
    # Pas de mode actuel → changement autorisé
    if current_mode is None:
        return True

    # Même mode → pas de changement
    if new_mode == current_mode:
        return False

    # Score inférieur → pas de changement
    if new_score <= current_score:
        return False

    # Hystérésis: vérifie la marge ET le temps minimum
    score_margin = (new_score - current_score) / max(0.01, current_score) * 100

    if score_margin < _HYSTERESIS_MARGIN:
        return False

    if time_since_change < _HYSTERESIS_MIN_TIME:
        return False

    return True


# ── Override manuel ─────────────────────────────────────────────────────────────

def set_manual_override(mode=None, hud=None):
    """
    Marque qu'un override manuel a eu lieu.
    Pause l'auto-mode pendant _OVERRIDE_PAUSE_DURATION minutes.
    """
    with _lock:
        _state["override_until"] = time.monotonic() + (_OVERRIDE_PAUSE_DURATION * 60)
        # Logguer l'override pour apprentissage
        _log_decision(context=_state.get("context"), mode=mode, hud=hud, override=True)
        # Persist
        _save_state()
    print(f"[AUTO] Override manuel détecté, pause pendant {_OVERRIDE_PAUSE_DURATION} min")


def _is_override_active():
    """Vérifie si l'override manuel est encore actif."""
    with _lock:
        return time.monotonic() < _state["override_until"]


# ── Apprentissage léger ───────────────────────────────────────────────────────────

def _load_learning():
    """Charge les poids d'apprentissage depuis le fichier."""
    try:
        if _LEARNING_FILE.exists():
            data = json.loads(_LEARNING_FILE.read_text())
            _state["learning_weights"] = data.get("weights", {})
    except Exception:
        pass


def _save_learning():
    """Sauvegarde les poids d'apprentissage."""
    try:
        _LEARNING_FILE.write_text(json.dumps({"weights": _state["learning_weights"]}, indent=2))
    except Exception:
        pass


def _log_decision(context, mode, hud, override=False):
    """Loggue une décision dans auto_mode_log.jsonl."""
    try:
        entry = {
            "timestamp": time.time(),
            "datetime": dt.now().isoformat(),
            "context": {
                "has_video": context.get("has_video"),
                "has_music": context.get("has_music"),
                "has_lyrics": context.get("has_lyrics"),
                "lyrics_synced": context.get("lyrics_synced"),
                "is_night": context.get("is_night"),
            },
            "decision": {
                "mode": mode,
                "hud": hud,
            },
            "override": override,
        }
        try:
            lock = _log_file_lock if '_log_file_lock' in globals() else None
            if lock:
                lock.acquire()
            try:
                with _LOG_FILE.open("a") as f:
                    f.write(json.dumps(entry) + "\n")
            finally:
                if lock:
                    lock.release()
        except Exception:
            pass
    except Exception:
        pass


_log_file_lock = threading.Lock()


def record_correction(from_mode, to_mode, context_key):
    """
    Enregistre une correction utilisateur pour ajuster les poids.
    """
    with _lock:
        # Incrémenter le compteur pour ce contexte
        if "corrections" not in _state["learning_weights"]:
            _state["learning_weights"]["corrections"] = {}

        key = f"{context_key}_{from_mode}_to_{to_mode}"
        _state["learning_weights"]["corrections"][key] = \
            _state["learning_weights"]["corrections"].get(key, 0) + 1

        # Ajuster les poids si trop de corrections
        count = _state["learning_weights"]["corrections"][key]

        # Exemple simple: si plus de 3 corrections pour un contexte donné,
        # ajuster le poids correspondant
        if count >= 3:
            if "video" in context_key and "lyrics" in context_key:
                # Augmenter le poids des lyrics synchronisés
                _state["learning_weights"]["w_lyrics_synced"] = \
                    _state["learning_weights"].get("w_lyrics_synced", 1.0) * 1.1
                print(f"[AUTO] Ajustement: w_lyrics_synced ↑ (correction #{count})")

        _save_learning()


# ─── Décision principale ────────────────────────────────────────────────────────

def determine_mode(context):
    """
    Détermine le mode approprié selon le contexte avec scoring et hystérésis.

    Returns:
        ((mode, hud), led_config)  # API compatible
    """
    global _state, _transition_to_idle, _last_music_mode

    with _lock:
        # Charger les poids d'apprentissage
        _load_learning()

        # Transitions intelligentes: détecter fin de musique
        has_music_now = context.get("has_music", False)
        had_music_before = _last_music_mode is not None

        if had_music_before and not has_music_now:
            # La musique s'arrête → enregistrer le dernier mode musical
            if _state["current_mode"] in ("lyrics", "audio_viz", "video+lyrics"):
                _last_music_mode = _state["current_mode"]
                _transition_to_idle = True
                print(f"[AUTO] Transition: musique finie, dernier mode: {_last_music_mode}")

        # Si on est en transition idle et la musique reprend
        if _transition_to_idle and has_music_now:
            _transition_to_idle = False
            print(f"[AUTO] Transition: musique reprise, fin transition idle")

        # Mettre à jour last_music_mode si on a de la musique
        if has_music_now and _state["current_mode"] in ("lyrics", "audio_viz", "video+lyrics"):
            _last_music_mode = _state["current_mode"]

        # Calculer les scores
        scores = _base_scores(context, _state["learning_weights"])

        # Transition intelligente: si on vient de perdre la musique,
        # privilégier audio_viz pendant une période avant ascii_hud
        if _transition_to_idle and not has_music_now:
            # Boost audio_viz pour la transition
            scores["audio_viz"] = max(scores.get("audio_viz", 0), 65.0)
            # Pénaliser ascii_hud pour retarder la transition
            scores["ascii_hud"] = min(scores.get("ascii_hud", 0), 40.0)

        # Trouver le meilleur mode
        best_mode = max(scores.keys(), key=lambda k: scores[k])
        best_score = scores[best_mode]

        # Vérifier l'hystérésis
        current_mode = _state.get("current_mode")
        current_score = _state.get("current_score", 0.0)
        time_since_change = time.monotonic() - _state.get("last_change_time", 0)

        should_change = _should_change_mode(
            current_mode, current_score, best_mode, best_score, time_since_change
        )

        if should_change:
            _state["current_mode"] = best_mode
            _state["current_score"] = best_score
            _state["last_change_time"] = time.monotonic()

            # Loguer la décision
            mode_combo = _MODE_COMBINATIONS[best_mode]
            _log_decision(context, mode_combo[0], mode_combo[1], override=False)

            # Si on arrive vraiment sur ascii_hud, terminer la transition
            if best_mode == "ascii_hud":
                _transition_to_idle = False
        else:
            # Garder le mode actuel
            best_mode = current_mode or "ascii_hud"  # Fallback

        # Récupérer la config
        mode_combo = _MODE_COMBINATIONS.get(best_mode, _MODE_COMBINATIONS["ascii_hud"])
        led_config = _LED_CONFIGS.get(best_mode, _LED_CONFIGS["ascii_hud"])

        _state["scores"] = scores
        _state["context"] = context

        return mode_combo, led_config


# ─── API publique (compatibilité avec code existant) ─────────────────────────────

def get_state():
    """Retourne l'état actuel pour debug."""
    with _lock:
        return {
            "current_mode": _state.get("current_mode"),
            "current_score": _state.get("current_score"),
            "scores": _state.get("scores", {}),
            "override_active": _is_override_active(),
            "context": _state.get("context"),
        }


def should_reevaluate(last_context, current_context):
    """
    Détermine si on doit réévaluer le mode.
    Retourne True si changement important du contexte.
    """
    if last_context is None:
        return True

    # Changements importants
    important_keys = [
        "has_music",
        "has_lyrics",
        "lyrics_synced",
        "video_playing",
        "is_night",
    ]

    for key in important_keys:
        if last_context.get(key) != current_context.get(key):
            return True

    return False


def format_context(context):
    """Format le contexte pour debug/affichage."""
    lines = [
        f"Video: {'YES' if context.get('has_video') else 'NO'}"
        + (f" ({context.get('video_duration'):.0f}s)" if context.get('video_duration') else "")
        + (f" PLAYING" if context.get('video_playing') else " PAUSED"),
        f"Music: {'YES' if context.get('has_music') else 'NO'}"
        + (f" (lvl: {context.get('music_level'):.2f})" if context.get('has_music') else ""),
        f"Lyrics: {'YES' if context.get('has_lyrics') else 'NO'}"
        + (f" (synced)" if context.get('lyrics_synced') else ""),
        f"Night: {'YES' if context.get('is_night') else 'NO'}",
    ]
    return " | ".join(lines)


# ── Persistence ─────────────────────────────────────────────────────────────────

def _save_state():
    """Sauvegarde l'état dans last_state.json."""
    try:
        _STATE_FILE.write_text(json.dumps({
            "mode": _state.get("current_mode"),
            "auto": True,  # Marqueur qu'on est en mode auto
        }, indent=2))
    except Exception:
        pass


# ── Test standalone ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[AUTO] Test du mode auto intelligent\n")
    ctx = detect_context()
    print(f"Contexte: {format_context(ctx)}")

    # Simuler quelques scénarios
    print("\n--- Scénarios de test ---")

    # Scénario 1: Musique + lyrics syncés
    ctx_test = {
        "has_video": False,
        "video_playing": False,
        "has_music": True,
        "music_level": 0.15,
        "music_level_ema": 0.12,
        "bpm": 120.0,
        "bpm_stable": True,
        "silence_prolonged": False,
        "has_lyrics": True,
        "lyrics_synced": True,
        "is_night": False,
    }
    scores = _base_scores(ctx_test, {})
    best = max(scores.keys(), key=lambda k: scores[k])
    print(f"Musique + Lyrics synced → {best} (score: {scores[best]:.1f})")

    # Scénario 2: Vidéo qui joue
    ctx_test = {
        "has_video": True,
        "video_playing": True,
        "has_music": False,
        "music_level": 0.0,
        "music_level_ema": 0.0,
        "bpm": None,
        "bpm_stable": False,
        "silence_prolonged": False,
        "has_lyrics": False,
        "lyrics_synced": False,
        "is_night": False,
    }
    scores = _base_scores(ctx_test, {})
    best = max(scores.keys(), key=lambda k: scores[k])
    print(f"Vidéo qui joue → {best} (score: {scores[best]:.1f})")

    # Scénario 3: Nuit, silence
    ctx_test = {
        "has_video": False,
        "video_playing": False,
        "has_music": False,
        "music_level": 0.0,
        "music_level_ema": 0.0,
        "bpm": None,
        "bpm_stable": False,
        "silence_prolonged": True,
        "has_lyrics": False,
        "lyrics_synced": False,
        "is_night": True,
    }
    scores = _base_scores(ctx_test, {})
    best = max(scores.keys(), key=lambda k: scores[k])
    print(f"Nuit + Silence → {best} (score: {scores[best]:.1f})")

    # Test hystérésis
    print("\n--- Test hystérésis ---")
    print(f"Marge requise: {_HYSTERESIS_MARGIN}%")
    print(f"Temps minimum: {_HYSTERESIS_MIN_TIME}s")

    # Scénario: lyrics → audio_viz (ne devrait pas changer immédiatement)
    _state["current_mode"] = "lyrics"
    _state["current_score"] = 90.0
    _state["last_change_time"] = time.monotonic() - 5  # 5s seulement

    ctx_test = {
        "has_video": False,
        "video_playing": False,
        "has_music": True,
        "music_level": 0.2,
        "music_level_ema": 0.15,
        "bpm": 120.0,
        "bpm_stable": True,
        "silence_prolonged": False,
        "has_lyrics": False,  # Lyrics disparus
        "lyrics_synced": False,
        "is_night": False,
    }
    scores = _base_scores(ctx_test, {})
    best = max(scores.keys(), key=lambda k: scores[k])

    should = _should_change_mode(
        _state["current_mode"],
        _state["current_score"],
        best,
        scores[best],
        5.0  # 5s seulement
    )
    print(f"Lyrics → Audio_viz (5s, marge {scores[best] - _state['current_score']:.1f}): {'CHANGE' if should else 'KEEP'}")

    # Après 15s, devrait changer
    should = _should_change_mode(
        _state["current_mode"],
        _state["current_score"],
        best,
        scores[best],
        15.0  # 15s
    )
    print(f"Lyrics → Audio_viz (15s, marge {scores[best] - _state['current_score']:.1f}): {'CHANGE' if should else 'KEEP'}")

    print("\n[AUTO] Test terminé")
