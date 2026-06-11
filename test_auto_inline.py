"""
test_auto_inline.py - Test inline pour auto_mode
"""

import time
import auto_mode

# Test scoring basique
print("=== Test Scoring ===")
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
scores = auto_mode._base_scores(ctx, {})
print("Scores musique+lyrics syncés:")
for k, v in sorted(scores.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v:.1f}")

# Test détermination
print("\n=== Test Détermination ===")
auto_mode._state["current_mode"] = None
auto_mode._state["last_change_time"] = 0
(mode, hud), led = auto_mode.determine_mode(ctx)
print(f"Contexte musique+lyrics -> {mode}+{hud}")

# Test silence
ctx2 = {
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
auto_mode._state["current_mode"] = None
(mode2, hud2), led2 = auto_mode.determine_mode(ctx2)
print(f"Contexte silence -> {mode2}+{hud2}")

# Test hystérésis
print("\n=== Test Hystérésis ===")
auto_mode._state["current_mode"] = "lyrics"
auto_mode._state["current_score"] = 90.0
auto_mode._state["last_change_time"] = time.monotonic() - 2  # 2s seulement

ctx3 = {
    "has_video": False,
    "video_playing": False,
    "has_music": True,
    "music_level": 0.2,
    "music_level_ema": 0.15,
    "bpm": 120.0,
    "bpm_stable": True,
    "silence_prolonged": False,
    "has_lyrics": False,
    "lyrics_synced": False,
    "is_night": False,
}

(mode3, hud3), led3 = auto_mode.determine_mode(ctx3)
should_keep = auto_mode._should_change_mode(
    "lyrics", 90.0, "audio_viz", auto_mode._state["scores"].get("audio_viz", 0), 2.0
)
print(f"Changement après 2s: {should_keep} (devrait être False)")

# Test après temps suffisant
should_keep2 = auto_mode._should_change_mode(
    "lyrics", 90.0, "audio_viz", auto_mode._state["scores"].get("audio_viz", 0), 15.0
)
print(f"Changement après 15s: {should_keep2} (devrait être True)")

print("\n=== Tests terminés ===")
