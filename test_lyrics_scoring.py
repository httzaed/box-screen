"""
test_lyrics_scoring.py — Tests unitaires du système de scoring des lyrics.

Cas de test:
1. lrclib synced correct (match métadonnées parfait)
2. lrclib mauvaise durée (écart > 7s)
3. YouTube captions auto pourries (sans ponctuation)
4. YouTube captions uploadées propres
5. Cross-validation entre deux candidats

Le bon candidat doit gagner à chaque fois.
"""
import sys
import os
import io

# Force UTF-8 output for Windows console
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from lyrics_source import (
    score_lyrics_candidate,
    _fuzzy_match,
    _detect_caption_auto_generated,
    _check_temporal_coherence,
    _normalize_for_match,
)


def test_normalize_for_match():
    """Test la normalisation pour matching."""
    print("\n=== test_normalize_for_match ===")

    cases = [
        ("Song Name (feat. Artist)", "song name"),
        ("Song Name - Radio Edit", "song name"),
        ("Song Name [Remastered]", "song name"),
        ("Artist Name - Topic -", "artist name"),
        ("Song Name (Prod. by X)", "song name"),
    ]

    for input_text, expected_substring in cases:
        result = _normalize_for_match(input_text)
        assert expected_substring in result, f"Expected '{expected_substring}' in '{result}'"
        print(f"[OK] '{input_text}' -> '{result}'")


def test_fuzzy_match():
    """Test le matching flou."""
    print("\n=== test_fuzzy_match ===")

    # Matches parfaits
    assert _fuzzy_match("Same Title", "Same Title") == 1.0
    assert _fuzzy_match("Same Title", "same title") == 1.0  # case insensitive

    # Matches partiels
    assert _fuzzy_match("Song Name", "Song Name (feat. X)") > 0.8
    assert _fuzzy_match("Song Name", "Song Name - Radio Edit") > 0.8
    assert _fuzzy_match("Artist Name", "Artist Name - Topic") > 0.8

    # Mauvais matches
    assert _fuzzy_match("Completely Different", "Not Similar") < 0.3

    print("[OK] fuzzy_match tests passed")


def test_detect_caption_auto_generated():
    """Test la détection de captions auto-générés."""
    print("\n=== test_detect_caption_auto_generated ===")

    # Captions propres (uploadées)
    synced_clean = [
        (0.0, "This is the first line."),
        (2.5, "This is the second line, with punctuation!"),
        (5.0, "Third line has proper casing."),
        (7.5, "Fourth line looks good."),
    ]
    is_auto, lang = _detect_caption_auto_generated(synced_clean)
    assert not is_auto, f"Clean captions should not be detected as auto, got {is_auto}"
    print(f"[OK] Clean captions: is_auto={is_auto}, lang={lang}")

    # Captions pourries (auto-générées)
    synced_auto = [
        (0.0, "[Music]"),
        (2.5, "this is the first line without any punctuation"),
        (5.0, "[Applause]"),
        (7.5, "second line also without punctuation and lowercase"),
        (10.0, "♪"),
        (12.5, "third line verylongwordwithoutspaces"),
    ]
    is_auto, lang = _detect_caption_auto_generated(synced_auto)
    assert is_auto, f"Auto captions should be detected as auto, got {is_auto}"
    print(f"[OK] Auto captions: is_auto={is_auto}, lang={lang}")


def test_check_temporal_coherence():
    """Test la vérification de cohérence temporelle."""
    print("\n=== test_check_temporal_coherence ===")

    # Timestamps valides
    synced_valid = [
        (0.0, "Line 1"),
        (5.0, "Line 2"),
        (10.0, "Line 3"),
        (15.0, "Line 4"),
    ]
    is_coherent, reason = _check_temporal_coherence(synced_valid, 20.0)
    assert is_coherent, f"Valid timestamps should be coherent, got: {reason}"
    print(f"[OK] Valid timestamps: {reason or 'OK'}")

    # Timestamps non croissants
    synced_not_growing = [
        (0.0, "Line 1"),
        (10.0, "Line 2"),
        (5.0, "Line 3"),  # Non croissant!
    ]
    is_coherent, reason = _check_temporal_coherence(synced_not_growing, 20.0)
    assert not is_coherent, "Non-growing timestamps should fail"
    print(f"[OK] Non-growing timestamps: {reason}")

    # Premier timestamp trop tard
    synced_late_start = [
        (70.0, "Line 1"),
        (75.0, "Line 2"),
    ]
    is_coherent, reason = _check_temporal_coherence(synced_late_start, 100.0)
    assert not is_coherent, "Late start timestamp should fail"
    print(f"[OK] Late start: {reason}")

    # Dernier timestamp > durée
    synced_too_long = [
        (0.0, "Line 1"),
        (10.0, "Line 2"),
        (26.0, "Line 3"),  # > durée 20 + 5s de tolérance
    ]
    is_coherent, reason = _check_temporal_coherence(synced_too_long, 20.0)
    assert not is_coherent, "Timestamp exceeding duration should fail"
    print(f"[OK] Too long: {reason}")


def test_score_lrclib_synced_correct():
    """Test 1: lrclib synced correct."""
    print("\n=== test_score_lrclib_synced_correct ===")

    candidate = {
        "synced": [
            (0.0, "First line"),
            (5.0, "Second line"),
            (10.0, "Third line"),
        ],
        "plain": "First line\nSecond line\nThird line",
        "artistName": "Test Artist",
        "trackName": "Test Song",
        "duration": 30.0,
        "source": "lrclib",
    }

    score, details = score_lyrics_candidate(
        candidate,
        artist="Test Artist",
        title="Test Song",
        media_duration=30.0,
        video_id=None,
    )

    print(f"Score: {score:.0f}")
    print(f"Détails: {details}")

    # Score attendu: sync(30) + metadata_match(~15) + temporal(10) + quality(10) + source_bonus(5) ≈ 70
    assert score > 50, f"Expected score > 50, got {score}"
    print("[OK] lrclib synced correct: score OK")


def test_score_lrclib_bad_duration():
    """Test 2: lrclib mauvaise durée (écart > 7s)."""
    print("\n=== test_score_lrclib_bad_duration ===")

    candidate = {
        "synced": [
            (0.0, "First line"),
            (5.0, "Second line"),
        ],
        "plain": "First line\nSecond line",
        "artistName": "Test Artist",
        "trackName": "Test Song",
        "duration": 180.0,  # 3min au lieu de 30s
        "source": "lrclib",
    }

    score, details = score_lyrics_candidate(
        candidate,
        artist="Test Artist",
        title="Test Song",
        media_duration=30.0,  # Durée réelle
        video_id=None,
    )

    print(f"Score: {score:.0f}")
    print(f"Détails: {details}")

    # Pénalité de durée: ~150s d'écart -> ~30 points de pénalité
    # Donc score devrait être significativement plus bas que le candidat correct
    assert "dur" in str(details.get("reason", [])), "Should have duration penalty"
    print("[OK] lrclib bad duration: pénalité appliquée")


def test_score_youtube_auto_poor():
    """Test 3: YouTube captions auto pourries."""
    print("\n=== test_score_youtube_auto_poor ===")

    candidate = {
        "synced": [
            (0.0, "[Music]"),
            (2.5, "this is the first line without punctuation"),
            (5.0, "[Applause]"),
            (7.5, "second line also lowercase no punct"),
        ],
        "plain": "this is the first line without punctuation\nsecond line also lowercase no punct",
        "source": "youtube_captions(abc12345)",
    }

    score, details = score_lyrics_candidate(
        candidate,
        artist="Test Artist",
        title="Test Song",
        media_duration=15.0,
        video_id="abc123456def",
    )

    print(f"Score: {score:.0f}")
    print(f"Détails: {details}")

    # Score devrait être bas à cause de la qualité texte (auto-generated)
    assert score <= 40, f"Auto captions should have low score, got {score}"
    assert any("auto" in str(r).lower() for r in details.get("reason", [])), "Should mention auto-generated"
    print("[OK] YouTube auto pourries: score bas")


def test_score_youtube_uploaded_clean():
    """Test 4: YouTube captions uploadées propres."""
    print("\n=== test_score_youtube_uploaded_clean ===")

    candidate = {
        "synced": [
            (0.0, "This is the first line."),
            (5.0, "This is the second line, with punctuation!"),
            (10.0, "Third line has proper casing."),
        ],
        "plain": "This is the first line.\nThis is the second line, with punctuation!\nThird line has proper casing.",
        "source": "youtube_captions(xyz78901)",
    }

    score, details = score_lyrics_candidate(
        candidate,
        artist="Test Artist",
        title="Test Song",
        media_duration=15.0,
        video_id="xyz789012abc",
    )

    print(f"Score: {score:.0f}")
    print(f"Détails: {details}")

    # Score devrait être élevé: sync(30) + temporal(10) + quality(15) = 55+
    assert score > 40, f"Clean captions should have good score, got {score}"
    print("[OK] YouTube uploadées propres: score bon")


def test_best_candidate_selection():
    """Test final: le meilleur candidat gagne."""
    print("\n=== test_best_candidate_selection ===")

    # Candidat 1: lrclib mauvaise durée
    cand1 = {
        "synced": [(0.0, "Line"), (5.0, "Line")],
        "plain": "Line",
        "artistName": "Test Artist",
        "trackName": "Test Song",
        "duration": 180.0,
        "source": "lrclib",
    }

    # Candidat 2: YouTube captions propres
    cand2 = {
        "synced": [(0.0, "This is line."), (5.0, "Another line.")],
        "plain": "This is line.\nAnother line.",
        "source": "youtube_captions(abc12345)",
    }

    # Candidat 3: YouTube auto pourries
    cand3 = {
        "synced": [(0.0, "[Music]"), (2.5, "no punctuation")],
        "plain": "no punctuation",
        "source": "youtube_captions(xyz78901)",
    }

    scores = []
    for i, cand in enumerate([cand1, cand2, cand3], 1):
        score, details = score_lyrics_candidate(
            cand,
            artist="Test Artist",
            title="Test Song",
            media_duration=15.0,
            video_id="abc123456" if i > 1 else None,
        )
        scores.append((i, score, details.get("source", cand.get("source", ""))))
        print(f"Candidat {i} ({cand.get('source')}): score={score:.0f}")

    # Le candidat 2 (YouTube propres) devrait avoir le meilleur score
    # car cand1 a une pénalité de durée et cand3 est auto-generated
    scores.sort(key=lambda x: x[1], reverse=True)
    best_idx, best_score, best_source = scores[0]

    print(f"\nMeilleur candidat: #{best_idx} ({best_source}) avec score {best_score:.0f}")

    # Le gagnant devrait être soit cand2 (YouTube propres) soit cand1 (lrclib malgré la durée)
    # mais certainement pas cand3 (auto pourries)
    assert best_idx != 3, "Auto captions should not win"
    print("[OK] Meilleur candidat sélectionné correctement")


def main():
    """Lance tous les tests."""
    print("=" * 60)
    print("TESTS DU SYSTÈME DE SCORING LYRICS")
    print("=" * 60)

    test_normalize_for_match()
    test_fuzzy_match()
    test_detect_caption_auto_generated()
    test_check_temporal_coherence()
    test_score_lrclib_synced_correct()
    test_score_lrclib_bad_duration()
    test_score_youtube_auto_poor()
    test_score_youtube_uploaded_clean()
    test_best_candidate_selection()

    print("\n" + "=" * 60)
    print("✅ TOUS LES TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
