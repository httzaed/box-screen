"""
lyrics_manager.py - Gestion des lyrics avec validation utilisateur
  Permet la recherche manuelle et le marquage des lyrics comme incorrects
"""
import json
import time
from pathlib import Path
from typing import Optional

# Import du module lyrics_source existant
try:
    import lyrics_source
except ImportError:
    lyrics_source = None
    print("[LYRICS MANAGER] WARNING: lyrics_source non disponible")

BLACKLIST_FILE = Path(__file__).parent / "lyrics_blacklist.json"


class LyricsManager:
    """Gestion des lyrics avec validation utilisateur"""

    def __init__(self):
        self._blacklist = self._load_blacklist()
        self._temp_skip = set()  # (artist, title, source) pour session

    def search_manual(self, artist: str, title: str, video_id: Optional[str] = None) -> dict:
        """
        Recherche manuelle - retourne la meilleure source automatiquement

        Args:
            artist: Nom de l'artiste
            title: Titre de la chanson
            video_id: Optionnel, ID YouTube pour les captions

        Returns:
            dict avec {"lyrics": {...}, "source": "...", "score": 85}
            ou {"error": "no_lyrics_found"}
        """
        if lyrics_source is None:
            return {"error": "lyrics_source_not_available"}

        try:
            # Réutiliser lyrics_source._load_lyrics_for() avec fast_first=False
            result = lyrics_source._load_lyrics_for(
                artist, title, video_id, fast_first=False
            )

            if result:
                score, _ = lyrics_source.score_lyrics_candidate(
                    result, artist, title, None, video_id
                )
                return {
                    "lyrics": result,
                    "source": result.get("source", "unknown"),
                    "score": score
                }
        except Exception as e:
            print(f"[LYRICS MANAGER] Erreur recherche: {e}")

        return {"error": "no_lyrics_found"}

    def flag_temp(self, artist: str, title: str, source: str) -> None:
        """
        Marque temporairement (session uniquement)

        Args:
            artist: Nom de l'artiste
            title: Titre de la chanson
            source: Source à skipper (lrclib, youtube, genius, etc.)
        """
        key = (artist.lower(), title.lower(), source)
        self._temp_skip.add(key)
        print(f"[LYRICS MANAGER] Skip temporaire: {artist} - {title} ({source})")

    def flag_permanent(self, artist: str, title: str, source: str,
                      reason: str = "user_reported") -> None:
        """
        Marque de manière permanente - sauvegarde dans JSON

        Args:
            artist: Nom de l'artiste
            title: Titre de la chanson
            source: Source à blacklist
            reason: Raison du blacklist
        """
        key = f"{artist.lower()}|{title.lower()}|{source}"
        self._blacklist[key] = {
            "reason": reason,
            "timestamp": time.time()
        }
        self._save_blacklist()
        print(f"[LYRICS MANAGER] Blacklist: {artist} - {title} ({source})")

    def is_blacklisted(self, artist: str, title: str, source: str) -> bool:
        """
        Vérifie si cette source est blacklistée pour ce titre

        Args:
            artist: Nom de l'artiste
            title: Titre de la chanson
            source: Source à vérifier

        Returns:
            True si la source est blacklistée (temporaire ou permanent)
        """
        # Vérifier d'abord le skip temporaire
        key = (artist.lower(), title.lower(), source)
        if key in self._temp_skip:
            return True

        # Ensuite vérifier le blacklist permanent
        key_str = f"{artist.lower()}|{title.lower()}|{source}"
        return key_str in self._blacklist

    def get_blacklist(self) -> dict:
        """Retourne le blacklist complet"""
        return dict(self._blacklist)

    def clear_temp_skips(self) -> None:
        """Efface tous les skips temporaires (fin de session)"""
        self._temp_skip.clear()
        print("[LYRICS MANAGER] Skips temporaires effacés")

    def _load_blacklist(self) -> dict:
        """Charge le blacklist depuis le fichier JSON"""
        try:
            if BLACKLIST_FILE.exists():
                return json.loads(BLACKLIST_FILE.read_text())
        except Exception as e:
            print(f"[LYRICS MANAGER] Erreur chargement blacklist: {e}")
        return {}

    def _save_blacklist(self) -> None:
        """Sauvegarde le blacklist vers le fichier JSON"""
        try:
            BLACKLIST_FILE.write_text(json.dumps(self._blacklist, indent=2))
        except Exception as e:
            print(f"[LYRICS MANAGER] Erreur sauvegarde blacklist: {e}")


# Instance globale
_manager: Optional[LyricsManager] = None


def get_manager() -> LyricsManager:
    """Retourne l'instance singleton du manager"""
    global _manager
    if _manager is None:
        _manager = LyricsManager()
    return _manager


# API simple pour les tests
if __name__ == "__main__":
    manager = LyricsManager()

    # Test recherche manuelle
    print("\n=== Test recherche manuelle ===")
    result = manager.search_manual("Tyler, The Creator", "See You Again")
    print(f"Résultat: {result.get('source', 'N/A')}")

    # Test blacklist
    print("\n=== Test blacklist ===")
    manager.flag_permanent("Test Artist", "Test Title", "test_source", "test")
    print(f"Blacklisté: {manager.is_blacklisted('Test Artist', 'Test Title', 'test_source')}")
