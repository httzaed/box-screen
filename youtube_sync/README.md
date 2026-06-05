# Box Screen — YouTube Lyrics Sync Extension

Chrome/Edge extension pour sync les lyrics avec YouTube en temps réel.

## Installation

### Chrome/Edge:
1. Ouvrir `chrome://extensions` (ou `edge://extensions`)
2. Activer "Mode développeur" (haut droite)
3. Cliquer "Charger l'extension non empaquetée"
4. Sélectionner le dossier `youtube_sync/`
5. L'extension apparaît dans la liste

## Utilisation

1. Lancer `python launcher.py`
2. Ouvrir YouTube dans Chrome/Edge
3. Lire une vidéo
4. L'extension envoie la position automatiquement à localhost:7420
5. Passer le HUD en mode "lyrics" (ctrl+shift+↑↓)

## Comment ça marche

- L'extension lit le temps YouTube via `player.getCurrentTime()`
- Envoie artiste, titre, position à notre API HTTP toutes les 500ms
- Le module lyrics_source utilise cette position pour sync les lyrics
- Priorité sur SMTC (Windows Media Session)

## Désinstallation

Aller dans `chrome://extensions` et cliquer "Supprimer"
