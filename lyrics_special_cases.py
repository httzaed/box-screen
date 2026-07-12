"""
lyrics_special_cases.py — Gestion des cas particuliers pour les lyrics

Détecte et gère :
1. City pop / titres japonais (paroles en japonais + romaji + traduction)
2. Beats, type beats, instrumentaux (sans paroles)
3. DJ mixes (sans paroles)
4. Mashups (détection automatique multi-sources)

Ce module complète lyrics_source.py en identifiant les cas qui nécessitent
un traitement spécial.
"""
import re
import pathlib
from typing import Literal, Optional

# ── Patterns de détection ─────────────────────────────────────────────────────

# Indicateurs de beats/instrumentaux
INSTRUMENTAL_PATTERNS = [
    r'\binstrumental\b',
    r'\bkaraoke\b',
    r'\btype beat\b',
    r'\btypebeat\b',
    r'\bbeat\s*$',  # "beat" en fin de titre (ex: "chill beat")
    r'\btype\s*beat\s*$',  # "type beat" en fin de titre
    r'\binstrument\s*\b',
    r'\bno\s+vocals?\b',
    r'\bminus\b',
    r'\bbacking\s+track\b',
]

# Indicateurs de chaînes YouTube suspectes (playlists, remixers, etc.)
SUSPICIOUS_CHANNEL_PATTERNS = [
    r'\([^)]*mimi[^)]*\)',  # (mimijil), (mimix), etc.
    r'\([^)]*dj\s*[^)]*\)',  # (DJ something)
    r'\([^)]*mix[^)]*\)',  # (mix), (remix), etc.
    r'\([^)]*edit[^)]*\)',  # (edit), (edited), etc.
    r'\([^)]*prod[^)]*\)',  # (prod by X)
    r'\bchannel\s*$',  # "XYZ Channel" en fin de nom
    r'\bofficial\s*$',  # "XYZ Official" en fin de nom (si pas de "Topic")
]

# Indicateurs DJ mixes
DJ_MIX_PATTERNS = [
    r'\bdj\s+\w+\s+(mix|set|blend|edit|flip|dub)\b',
    r'\b\d{4}-\d{2}-\d{2}\b',  # Dates type "2024-05-15"
    r'\b(at\s+)?\w+\s+(club|radio|fm|session|live)\s*:\s*\d{2}:\d{2}\b',  "Radio sets"
    r'\bcontinuous\s+mix\b',
    r'\bnon[- ]?stop\b',
    r'\bset\s*:\s*\w+\b',
    r'\b\d{2,3}\s*min\s+mix\b',
    r'\b\d{1,2}\s*hr\s+mix\b',
    r'\bdeephouse\b',
    r'\btech\s+house\b',
    r'\bdisco\s+mix\b',
]

# Indicateurs japonais (caractères)
JP_CHARS = r'[぀-ゟ゠-ヿ一-龯]'

# ── Détection ───────────────────────────────────────────────────────────────────

def detect_case_type(artist: str, title: str) -> Optional[Literal[
    "instrumental", "dj_mix", "japanese", "mashup", "normal"
]]:
    """
    Détecte le type de cas particulier pour un morceau.

    Retourne:
    - "instrumental": beat/instrumental sans paroles
    - "dj_mix": DJ set/mix continu
    - "japanese": contient des caractères japonais
    - "mashup": indique un mashup (multi-sources)
    - "normal": cas standard

    Priorité: instrumental > dj_mix > japanese > mashup > normal
    """
    combined = f"{artist} - {title}".lower()

    # 1) Instrumental/beat = pas de lyrics
    for pattern in INSTRUMENTAL_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return "instrumental"

    # 2) DJ mix = pas de lyrics (ou lyrics multiples)
    for pattern in DJ_MIX_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return "dj_mix"

    # 3) Japonais : recherche de caractères japonais
    if re.search(JP_CHARS, artist + title):
        return "japanese"

    # 4) Mashup : indicateurs de multi-sources
    # Note: "vs" entre mots différents (pas dans "version")
    # " x " seulement si c'est une collaboration entre artistes (pas "extended")
    mashup_indicators = [
        r'(\w+)\s+vs\.?\s+(\w+)',  # "Song1 vs Song2" (mais pas "version")
        r'(\w+)\s+x\s+(\w+)',       # "Artist1 x Artist2" (collaboration)
        r'\bmashup\b',
        r'\bblend\b',
        r'\bflip\b',
        r' (bootleg|boot)\b',
        r'\bmix\s*\([^)]*\)',       # "(mix)" avec quelque chose dedans
    ]

    # "vs" détecté si entouré de mots (pas dans "version", "inverse", etc.)
    if re.search(r'(\w+)\s+vs\.?\s+(\w+)', combined, re.IGNORECASE):
        # Vérifier que ce n'est pas dans "version" ou similaire
        if not re.search(r'\bversion\b|\bconverse\b|bverse\b', combined, re.IGNORECASE):
            return "mashup"

    for pattern in mashup_indicators:
        if re.search(pattern, combined, re.IGNORECASE):
            return "mashup"

    return "normal"


def should_skip_lyrics(artist: str, title: str) -> tuple[bool, str]:
    """
    Détermine si on doit éviter de chercher des lyrics.

    Retourne: (skip: bool, reason: str)
    """
    case_type = detect_case_type(artist, title)

    if case_type == "instrumental":
        return True, "beat/instrumental (sans paroles)"
    if case_type == "dj_mix":
        return True, "DJ mix/set (sans paroles)"

    return False, ""


def is_japanese_content(artist: str, title: str) -> bool:
    """Détecte si le contenu est en japonais."""
    return detect_case_type(artist, title) == "japanese"


def is_mashup(artist: str, title: str) -> bool:
    """Détecte si c'est probablement un mashup."""
    return detect_case_type(artist, title) == "mashup"


def extract_mashup_sources(artist: str, title: str) -> list[dict]:
    """
    Extrait les sources d'un mashup depuis le titre.

    Analyse les patterns:
    - "Artist1 - Song1 vs Song2"
    - "Artist1 - Song1 x Artist2 - Song2"
    - "Artist1, Artist2 - Mashup Name"

    Retourne: [{"artist": str, "title": str}, ...]
    """
    combined = f"{artist} - {title}"
    sources = []

    # Pattern 1: "Artist1 - Song1 vs Artist2 - Song2"
    vs_match = re.search(r'(.+?)\s+vs\s+(.+)', combined, re.IGNORECASE)
    if vs_match:
        part1, part2 = vs_match.groups()
        sources.extend(_parse_source_part(part1.strip()))
        sources.extend(_parse_source_part(part2.strip()))

    # Pattern 2: "Artist1 - Song1 x Artist2 - Song2" (x = collaboration)
    elif ' x ' in combined.lower():
        parts = re.split(r'\s+x\s+', combined, flags=re.IGNORECASE)
        for part in parts:
            sources.extend(_parse_source_part(part.strip()))

    # Pattern 3: "Artist1, Artist2 - Title" (liste d'artistes)
    elif ', ' in artist:
        artists = [a.strip() for a in artist.split(',')]
        for a in artists:
            sources.append({"artist": a, "title": title.strip()})

    else:
        # Pas de pattern reconnu
        return []

    # Nettoyer et dédupliquer
    seen = set()
    unique_sources = []
    for src in sources:
        key = f"{src.get('artist', '')}|{src.get('title', '')}".lower()
        if key and key not in seen:
            seen.add(key)
            unique_sources.append(src)

    return unique_sources


def _parse_source_part(part: str) -> list[dict]:
    """
    Parse une partie de mashup en artist/title.

    Accepte: "Artist - Title" ou juste "Title"
    """
    if ' - ' in part:
        artist, title = part.split(' - ', 1)
        return [{"artist": artist.strip(), "title": title.strip()}]
    else:
        return [{"title": part.strip()}]


# ── Gestion des paroles japonaises ─────────────────────────────────────────────

JAPANESE_SOURCES = [
    "uta-net.com",
    "j-lyric.net",
    "kget.jp",
    "music.goo.ne.jp",
]

def fetch_japanese_lyrics(artist: str, title: str) -> Optional[dict]:
    """
    Tente de récupérer les paroles japonaises avec translittération.

    Stratégie:
    1) Cherche sur des sites de paroles JP
    2) Fournit: texte original, romaji, traduction EN

    Retourne: {"original": str, "romaji": str, "translation": str} ou None
    """
    import urllib.request
    import urllib.parse
    import re

    # Pour l'instant, on détecte mais on n'implémente pas encore
    # le fetch réel (nécessite du scraping web)
    print(f"[LYRICS-JP] Contenu japonais détecté: {artist} - {title}")
    print(f"[LYRICS-JP] Fetch non implémenté - utilisez les APIs standards")

    return None


# ── Normalisation des titres pour les versions alternatives ───────────────────

def normalize_alternative_title(title: str) -> str:
    """
    Normalise un titre de version alternative pour trouver les paroles originales.

    Retire les suffixes courants des versions alternatives:
    - (slowed + reverb), (sped up), (extended)
    - (remix), (edit), (flip)
    - (mashup), (prod. X), (DJ dub)
    - [Official], [Audio], etc.

    Exemple:
    "Song Name (slowed + reverb)" → "Song Name"
    "Song Name - Extended Version" → "Song Name"
    "Artist - Song (prod. Beatmaker)" → "Artist - Song"
    """
    t = title.strip()

    # Retire les suffixes entre parenthèses
    alt_suffixes = [
        r'\s*\([^)]*slowed[^)]*\)',  # (slowed + reverb), (slowed)
        r'\s*\([^)]*sped[^)]*\)',     # (sped up)
        r'\s*\([^)]*extended[^)]*\)',  # (extended)
        r'\s*\([^)]*remix[^)]*\)',     # (remix)
        r'\s*\([^)]*edit[^)]*\)',      # (edit)
        r'\s*\([^)]*flip[^)]*\)',      # (flip)
        r'\s*\([^)]*mashup[^)]*\)',    # (mashup)
        r'\s*\([^)]*prod\.\s*[^)]*\)',  # (prod. X)
        r'\s*\([^)]*DJ[^)]*\)',        # (DJ X)
        r'\s*\([^)]*dub[^)]*\)',       # (dub)
        r'\s*\([^)]*live[^)]*\)',      # (live)
        r'\s*\([^)]*version[^)]*\)',   # (version)
        r'\s*\([^)]*original[^)]*\)',  # (original)
        r'\s*\([^)]*official[^)]*\)',   # (official)
        r'\s*\[[^\]]*audio[^\]]*\]',   # [Audio]
        r'\s*\[[^\]]*video[^\]]*\]',   # [Video]
        r'\s*\[[^\]]*lyrics[^\]]*\]',  # [Lyrics]
    ]

    for pattern in alt_suffixes:
        t = re.sub(pattern, '', t, flags=re.IGNORECASE).strip()

    # Retire "vs X" si présent (pour mashups)
    t = re.sub(r'\s+vs\.?\s+.*', '', t, flags=re.IGNORECASE).strip()

    # Retire les suffixes après tiret
    dash_suffixes = [
        r'\s*[-–]\s*slowed(?:\s*\+\s*reverb)?',
        r'\s*[-–]\s*sped\s+up',
        r'\s*[-–]\s*extended',
        r'\s*[-–]\s*remix',
        r'\s*[-–]\s*edit',
        r'\s*[-–]\s*version',
        r'\s*[-–]\s*official',
        r'\s*[-–]\s*audio',
        r'\s*[-–]\s*video',
    ]

    for pattern in dash_suffixes:
        t = re.sub(pattern, '', t, flags=re.IGNORECASE).strip()

    return t


# ── API pour lyrics_source.py ───────────────────────────────────────────────────

def analyze_track(artist: str, title: str) -> dict:
    """
    Analyse complète d'un morceau pour détecter les cas particuliers.

    Retourne: {
        "case_type": "normal|instrumental|dj_mix|japanese|mashup",
        "should_skip": bool,
        "skip_reason": str,
        "is_japanese": bool,
        "is_mashup": bool,
        "mashup_sources": list,
        "normalized_title": str,
    }
    """
    case_type = detect_case_type(artist, title)
    should_skip, skip_reason = should_skip_lyrics(artist, title)

    return {
        "case_type": case_type,
        "should_skip": should_skip,
        "skip_reason": skip_reason,
        "is_japanese": case_type == "japanese",
        "is_mashup": case_type == "mashup",
        "mashup_sources": extract_mashup_sources(artist, title) if case_type == "mashup" else [],
        "normalized_title": normalize_alternative_title(title),
    }


# ── Test ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import io

    # Force UTF-8 output pour Windows
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    test_cases = [
        ("playboi carti", "talk 2 me (prod. racki) extended"),
        ("playboi carti", "sky (slowed + reverb)"),
        ("EELF", "geods sorèd type beat"),
        ("DJ Koze", "Fabric 95 - 2024-09-15"),
        ("Makoto Matsushita", "終わりのない道"),
        ("Kanye West", "City of Lights vs. Flowers"),
        ("Various Artists", "Deep House Selection 2024"),
        ("beatmaker", "chill lo-fi instrumental"),
    ]

    print("=" * 80)
    print("TEST: Détection des cas particuliers")
    print("=" * 80)

    for artist, title in test_cases:
        result = analyze_track(artist, title)
        print(f"\n{artist} - {title}")
        print(f"  Type: {result['case_type']}")
        if result['should_skip']:
            print(f"  SKIP: {result['skip_reason']}")
        if result['is_mashup']:
            print(f"  Sources: {result['mashup_sources']}")
        print(f"  Normalisé: {result['normalized_title']}")
