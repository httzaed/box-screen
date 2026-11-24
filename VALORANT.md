# Valorant sur Box Screen

## Sources

1. **Client local** : quand Valorant tourne sur le PC, `valorant_source.py` lit le lockfile du Riot Client
   (`%LOCALAPPDATA%\Riot Games\Riot Client\Config\lockfile`) et la présence du joueur.
   Donne : menu / sélection d'agent / en partie, map, mode, score de la manche, taille du groupe.
   Lecture seule, aucune clé nécessaire.
2. **HenrikDev API** (non officielle, gratuite) : rang, RR, dernier gain/perte, peak, 5 derniers matchs.
   L'API publique de Tracker.gg ne gère pas Valorant, et Riot ne donne pas l'API Valorant aux clés perso.

## Mise en route

1. Rejoindre le Discord HenrikDev, puis générer une clé "Basic" sur https://api.henrikdev.xyz/dashboard/
2. Copier `valorant_config.example.json` en `valorant_config.json` et coller la clé dans `api_key`
   (ou définir la variable d'environnement `HDEV_API_KEY`).
3. `name` / `tag` sont optionnels : le Riot ID est lu dans le client dès que Valorant est lancé.
4. Redémarrer Phosphor / le launcher. La config est ensuite relue à chaud.

`valorant_config.json` et `valorant_cache.json` sont dans `.gitignore`.

## Panel (cascade)

Réglage `panel` (aussi dans le dashboard, carte 04 Valorant) :

- `after_lyrics` (défaut) : paroles, puis Valorant si le jeu est ouvert, puis visu, puis ASCII.
- `priority` : Valorant passe avant les paroles dès que le jeu est ouvert.
- `off` : jamais sur le panel (la carte du dashboard reste active).

"Visu forcée" passe aussi devant Valorant en mode `after_lyrics`.

## Rafraîchissement HenrikDev

Au lancement du jeu, 45 s puis 150 s après chaque fin de partie, toutes les 3 min jeu ouvert,
toutes les 10 min jeu fermé. Clé Basic = 30 requêtes/min, on en utilise 2 par rafraîchissement.

## Routes

- `GET /api/valorant` : état complet
- `POST /api/valorant/panel/<after_lyrics|priority|off>`
- `POST /api/valorant/refresh`

## Limites connues

- Le score live vient de la présence du chef de groupe : en groupe, c'est le score de son équipe (la tienne).
- Pas d'agent en direct (il faudrait les endpoints de partie du client, plus intrusifs). L'agent apparaît dans l'historique après la partie.

Tests : `python test_valorant_source.py`
