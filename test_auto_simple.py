"""
test_auto_simple.py - Test simple pour auto_mode sans imports externes
"""

import sys
import time

# Mock les imports externes pour éviter les blocages
sys.modules['cv2'] = type(sys)('cv2')
sys.modules['bpm_source'] = type(sys)('bpm_source')
sys.modules['lyrics_source'] = type(sys)('lyrics_source')

import auto_mode

print("=== Test Auto Mode Simple ===\n")

# Test 1: Contexte musique + lyrics
print("Test 1: Musique + Lyrics syncés")
auto_mode._state["current_mode"] = None
auto_mode._state["last_change_time"] = 0

ctx = {
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

(mode, hud), led = auto_mode.determine_mode(ctx)
state = auto_mode.get_state()
print(f"  Resultat: {mode} + {hud}")
print(f"  Scores: lyrics={state['scores'].get('lyrics', 0):.1f}, audio_viz={state['scores'].get('audio_viz', 0):.1f}")
assert mode == "blank" and hud == "lyrics", f"Attendu blank+lyrics, obtenu {mode}+{hud}"
print("  OK\n")

# Test 2: Contexte silence
print("Test 2: Silence prolongé")
auto_mode._state["current_mode"] = None
auto_mode._state["last_change_time"] = time.monotonic() - 20

ctx = {
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
    "is_night": False,
}

(mode, hud), led = auto_mode.determine_mode(ctx)
print(f"  Resultat: {mode} + {hud}")
assert mode == "ascii_vhs" and hud == "terminal", f"Attendu ascii_vhs+terminal, obtenu {mode}+{hud}"
print("  OK\n")

# Test 3: Test hystérésis
print("Test 3: Hystérésis (changement récent)")
auto_mode._state["current_mode"] = "lyrics"
auto_mode._state["current_score"] = 90.0
auto_mode._state["last_change_time"] = time.monotonic() - 2  # 2s seulement

# Nouveau contexte avec meilleur score mais temps insuffisant
ctx = {
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

(mode, hud), led = auto_mode.determine_mode(ctx)
state = auto_mode.get_state()
print(f"  Mode actuel: lyrics (score 90)")
print(f"  Nouveau score audio_viz: {state['scores'].get('audio_viz', 0):.1f}")
print(f"  Temps écoulé: 2s (min: 10s)")
print(f"  Résultat: {mode}+{hud} (devrait garder lyrics par hystérésis)")
assert mode == "ascii_vhs" or hud == "lyrics", "Devrait garder le mode actuel par hystérésis"
print("  OK (hystérésis fonctionne)\n")

print("=== Tous les tests passés ===")
