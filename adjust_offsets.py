#!/usr/bin/env python3
"""
Ajusteur d'offset lyrics - Script interactif
Permet de régler le sync de chaque chanson stockée dans le cache.
"""

import json
import pathlib
import sys

CACHE_FILE = "lyrics_cache.json"


def load_cache():
    """Charge le cache lyrics."""
    path = pathlib.Path(__file__).parent / CACHE_FILE
    if not path.exists():
        print(f"❌ Fichier cache non trouvé: {CACHE_FILE}")
        print("   Lance d'abord l'app pour créer le cache.")
        sys.exit(1)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Erreur lecture cache: {e}")
        sys.exit(1)


def save_cache(cache):
    """Sauvegarde le cache."""
    path = pathlib.Path(__file__).parent / CACHE_FILE
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f"✅ Cache sauvegardé ({len(cache)} chansons)")
    except Exception as e:
        print(f"❌ Erreur sauvegarde: {e}")


def list_songs(cache):
    """Liste toutes les chansons avec offset."""
    if not cache:
        print("📭 Cache vide")
        return

    print(f"\n📀 {len(cache)} chansons dans le cache:\n")
    print(f"{'#':<4} {'Offset':<8} {'Écoutes':<8} {'Artiste':<25} {'Titre'}")
    print("─" * 80)

    for i, (key, data) in enumerate(sorted(cache.items())):
        offset = data.get("offset", 0.5)
        count = data.get("play_count", 0)
        artist = data.get("artist", "?")[:24]
        title = data.get("title", "?")
        print(f"{i+1:<4} {offset:+.2f}s    {count:<8} {artist:<25} {title}")


def adjust_offset(cache, index):
    """Ajuste l'offset d'une chanson."""
    keys = sorted(cache.keys())
    if index < 0 or index >= len(keys):
        print("❌ Index invalide")
        return

    key = keys[index]
    data = cache[key]
    current = data.get("offset", 0.5)

    print(f"\n🎵 {data.get('artist', '?')} — {data.get('title', '?')}")
    print(f"   Offset actuel: {current:+.2f}s")
    print(f"   Écoutes: {data.get('play_count', 0)}")
    print(f"\n💡 Positif = retarder, Négatif = avancer")
    print(f"   Exemple: -0.5 avance les lyrics de 500ms")

    while True:
        try:
            val = input(f"\nNouvel offset (ou Entrée pour garder {current:+.2f}s, 'q' pour quitter): ").strip()
            if val.lower() == 'q':
                return
            if not val:
                return

            new_offset = float(val)
            data["offset"] = new_offset
            print(f"✅ Offset changé: {current:+.2f}s → {new_offset:+.2f}s")
            current = new_offset
        except ValueError:
            print("❌ Valeur invalide. Entre un nombre (ex: -0.5, +0.3, etc.)")


def delete_song(cache, index):
    """Supprime une chanson du cache."""
    keys = sorted(cache.keys())
    if index < 0 or index >= len(keys):
        print("❌ Index invalide")
        return

    key = keys[index]
    data = cache[key]
    print(f"\n🎵 {data.get('artist', '?')} — {data.get('title', '?')}")
    confirm = input("   Confirmer suppression? (o/N): ").strip().lower()
    if confirm == 'o':
        del cache[key]
        print(f"✅ Chanson supprimée")


def main():
    print("🎤 AJUSTEUR OFFSET LYRICS\n")

    cache = load_cache()
    list_songs(cache)

    while True:
        print("\n" + "─" * 40)
        print("\nCommandes:")
        print("  <num>    - Sélectionner chanson et ajuster offset")
        print("  l        - Liste des chansons")
        print("  d <num>  - Supprimer chanson")
        print("  s        - Sauvegarder et quitter")
        print("  q        - Quitter sans sauvegarder")

        cmd = input("\n❯ ").strip().lower()

        if not cmd:
            continue

        if cmd == 'l':
            list_songs(cache)

        elif cmd == 's':
            save_cache(cache)
            break

        elif cmd == 'q':
            print("👋 Bye (sans sauvegarder)")
            break

        elif cmd.startswith('d '):
            try:
                idx = int(cmd.split()[1]) - 1
                delete_song(cache, idx)
            except ValueError:
                print("❌ Commande invalide: d <numéro>")

        else:
            # C'est probablement un numéro
            try:
                idx = int(cmd) - 1
                adjust_offset(cache, idx)
            except ValueError:
                print("❌ Commande invalide")


if __name__ == "__main__":
    main()
