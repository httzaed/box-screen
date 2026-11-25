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
- Agent en direct : lu sur les serveurs de partie de Riot (glz) avec le jeton du client local. Ces endpoints ne sont pas officiels (lecture seule, 2 requêtes GET toutes les 2 s en sélection, une seule fois par partie ensuite). Pour couper : `"live_agent": false` dans `valorant_config.json`.

Tests : `python test_valorant_source.py`

## Mode jeu (interface rouge + dashboard)

Quand Valorant est lancé, `phosphor_ui.html` passe en rouge (palette « Rouges vifs et profonds » :
#D0021B, #B5121B, #960018, #5E0B12) avec un balayage plein écran, puis affiche un dashboard
Valorant qui tient sur l'écran (1920 × 1080 et 1920 × 950) :

1. Rang : emblème, RR, dernier gain, prochain rang, peak, écart au peak, Elo, échelle des rangs.
2. En direct : menu / sélection d'agent / en partie, map, mode, groupe, score et manches.
3. Session : bilan des 5 derniers matchs, % de victoires, série, K/D, KDA, ACS, kills, manches, HS.
4. Derniers matchs : agent, map, manches, K/D/A, ACS, il y a combien de temps.

- Bouton « Panneau de contrôle » : revient aux réglages (le thème reste rouge tant que le jeu est ouvert).
  Le bouton « ◆ Dashboard Valorant » de la barre du haut y retourne.
- Quand le jeu se ferme (2 relevés d'affilée, environ 6 s), l'interface repasse en vert.
- Tester sans le jeu : `http://127.0.0.1:7420/?valo=demo`. Ne jamais l'activer : `?valo=off`.
- Le thème rouge est généré à partir des couleurs vertes du CSS : si de nouvelles couleurs vertes
  sont ajoutées à la page, il faudra les ajouter au bloc « Mode Valorant » en fin de `<style>`.

## Artworks des agents

Pendant la sélection d'agent et la partie, le dashboard affiche l'artwork ASCII de l'agent choisi,
à droite du panneau « En direct » : estompé pendant que tu hésites, révélé avec un tampon
« Verrouillé » quand tu valides, puis fixe pendant la partie.

- Dépose un fichier texte par agent dans `agents_ascii/`, nommé d'après l'agent en minuscules :
  `reyna.txt`, `hush.txt`, `miks.txt` (`kay-o.txt` pour KAY/O).
- Les marges vides sont retirées et l'art est mis à l'échelle automatiquement.
- Pas de fichier pour l'agent : le panneau reste comme avant, avec juste le nom de l'agent.
- Un fichier ajouté pendant que la page est ouverte est pris en compte en moins d'une minute.
- Les noms viennent de valorant-api.com (cache dans `valorant_agents.json`, relu une fois par jour).

## En direct : équipe, côté, chrono, 10 agents, groupe

- Sélection d'agent : les 5 agents de ton équipe en temps réel (survolé / verrouillé, niveau),
  ton côté de départ (attaque ou défense), le chrono, le nombre d'adversaires verrouillés, partie classée ou non.
- En partie : les agents des 10 joueurs (alliés / adversaires) avec leur niveau, et ton côté actuel
  (il change à la mi-temps et en prolongation, en compétition, non classé et Premier).
- Dans les menus : ton groupe, avec le rang et le niveau de chaque membre (présences du client local).
- Aucun pseudo de joueur n'est affiché en direct, seulement les agents.
- Les niveaux masqués par un joueur (« cacher mon niveau ») restent masqués.

## Récap de fin de match

Quand HenrikDev a le nouveau match (45 s à 2 min 30 après la fin), un écran récap plein écran s'ouvre
tout seul dans les menus. Le bouton « Récap » du dashboard le rouvre à tout moment, « Fermer » le ferme,
et il se ferme seul au lancement de la sélection suivante.

- Ta partie : K/D/A, ACS, ADR, headshots, first bloods et first deaths, clutchs (1vN), 3K / 4K / Ace,
  meilleure manche, kills par arme, badge MVP du match ou de ton équipe.
- Manches : gagnée ou perdue, comment (E élimination, S spike explosé, D désamorcé, T temps écoulé),
  tes kills, cérémonies (Flawless, Thrifty, Clutch…), et la courbe d'économie des deux équipes.
- Tableau des scores des 10 joueurs.
- Pseudos : seulement le tien et ceux de ton groupe. Les autres joueurs sont désignés par leur agent,
  pour respecter le mode incognito.

## Images des agents

Le dashboard peut afficher une image par agent : dans les rangées d'équipe, les cartes des derniers matchs,
le tableau du récap, et pour ton agent quand il n'a pas d'artwork ASCII (grand panneau et flash).

- Dépose un fichier par agent dans `agents_icons/`, nommé en minuscules : `jett.png`, `kay-o.png`…
  (formats .png, .webp ou .jpg).
- Pour ton agent, l'ASCII de `agents_ascii/` passe en priorité ; son image sert quand même ailleurs.
- Un agent sans image s'affiche simplement avec son nom.

## Santé du PC

Panneau 05 du dashboard (en bas à droite), mis à jour toutes les 1,5 s : CPU (charge, fréquence),
GPU (charge, température, puissance), RAM, VRAM, courbe CPU / GPU sur 90 s, disque, réseau,
le processus le plus gourmand, et la conso de Valorant quand il tourne.

- Source : `pc_health.py` (psutil + nvidia-smi), route GET `/api/pc`.
- Température CPU : Windows ne la donne pas directement. Elle s'affiche si LibreHardwareMonitor
  tourne avec son serveur web activé (Options > Remote Web Server > Run, port 8085).

## Option « compétition uniquement » (touche C)

Bouton « Compét. C » en haut du dashboard, ou touche C quand le dashboard est affiché.
Une fois activée, la session (03), les derniers matchs (04), le bandeau défilant et le récap
ne prennent en compte que les matchs compétitifs. Le rang (01) reste toujours compétitif.

- Le serveur récupère à part les 5 derniers matchs compétitifs (une requête HenrikDev de plus par rafraîchissement).
- Le choix est retenu dans le navigateur, même après un rechargement.
- La touche C ne fait rien quand tu écris dans un champ, ni quand le dashboard n'est pas affiché.

## Écran Stats (touche S)

Bouton « Stats S » en haut du dashboard, ou touche S (S de nouveau ou « Fermer » pour revenir).
Tout tient sur une seule page, sans onglet.

- À gauche, sur toute la hauteur : ton main (l'agent que tu joues le plus pour le filtre choisi), en grand.
  Son artwork ASCII s'il est dans `agents_ascii/`, sinon son image de `agents_icons/`, sinon son nom en géant.
  En dessous : son % de victoires, K/D, ACS, headshots, et tes deux autres mains.
- En haut à droite : 5 phrases sur toi (ta map, ta bête noire, ton arme secrète, ton créneau, solo ou groupe),
  puis les chiffres clés (victoires, matchs, K/D, KDA, ACS, ADR, headshots, KAST, DDΔ).
- Graphiques : courbe du rang (par palier, avec les noms de rang), maps et agents (% de victoires avec un repère
  à 50 %), duels (premier kill, première mort, clutchs, ratio, 3K / 4K / Ace), armes, économie, côtés,
  moments de la journée, solo ou groupe (avec tes partenaires de groupe).
- En bas : records et faits (kills, ACS, séries, matchs serrés, prolongations, spikes, meilleur combo).
- Code couleur partout : blanc = 50 % ou plus, rouge = moins de 50 %.
- Sur un écran moins haut, les listes perdent leurs dernières lignes plutôt que de déborder.

Pour éviter qu'un agent ou une map joué une seule fois passe en tête, il faut au moins
3 parties (5 dès 60 parties) pour entrer dans « meilleurs » et « pires ».
Les stats se recalculent toutes les 30 minutes, dès qu'un nouveau match apparaît, et avec le bouton « Actualiser ».

Touche A (ou le bouton en haut à droite de l'écran Stats) : saison en cours ou tout l'historique.
La saison en cours est l'acte en cours d'après valorant-api.com (ex. « V26 · Act VI »),
à défaut celle de ton match le plus récent. A et C se combinent
(par exemple : saison en cours + compétition uniquement), et les deux choix sont retenus.

## Historique local et rattrapage

HenrikDev ne garde que les matchs qu'il a réussi à enregistrer (« stored matches »), ce qui peut en laisser
beaucoup de côté. Pour compléter :

- `valorant_history.json` : chaque match complet vu par le dashboard y est gardé (une ligne par match).
  Les stats se calculent sur ce fichier fusionné avec ce que HenrikDev a stocké, sans doublon.
- Rattrapage : au démarrage (puis une fois par jour au plus), la liste de matchs est remontée page par page,
  10 matchs par requête : jusqu'à 30 pages en compétition, puis 15 pages tous modes, avec une pause de
  2,5 s entre deux requêtes pour respecter la limite de HenrikDev. Il tourne en arrière-plan.
- Le journal indique « Rattrapage terminé : N matchs ajoutés », et l'écran Stats affiche en haut
  le nombre de matchs connus et leur origine.
- Jusqu'où Riot laisse remonter n'est pas documenté : le premier rattrapage le dira.
- Supprimer `valorant_history.json` remet l'historique local à zéro (il sera reconstruit au rattrapage suivant).

## Écran Stats : d'où viennent les données

- Le détail (duels, KAST, armes, économie, côtés, spike, groupe) n'existe que pour les matchs vus
  en entier, c'est-à-dire ceux de l'historique local (depuis septembre 2024) : la page le précise.
- Le DDΔ et les combos utilisent tout l'historique.
- Après la mise à jour, l'historique local passe au format 2 et le rattrapage se relance une fois
  pour récupérer ce détail.

## Écran en partie (automatique)

Il s'ouvre tout seul pendant un match à manches (compétition, non classée, premier, vélocité, spike rush,
réplication) et se ferme à la fin. Échap (ou « Dashboard ») bascule avec le dashboard. Même style que Stats.

- À gauche : ton agent en grand, avec tes stats avec lui (partout et sur cette map).
- En haut : la map, le score en énorme (le chiffre qui change s'anime), la manche, la mi-temps ou la prolongation,
  la durée, et un bandeau de ce qui compte (balle de match, série, remontada, mi-temps, qui mène).
- Chiffres : chances de victoire, côté actuel, manches qu'il vous faut et qu'il leur faut, série de manches,
  meilleure avance, pire retard, score à la pause, ton % sur la map.
- Fil du match : chaque manche gagnée (blanc) ou perdue (rouge), et l'écart au score (ligne dorée).
  Les manches jouées avant l'ouverture de la page apparaissent en gris (on ne sait pas qui les a gagnées).
- Chances de victoire : une estimation, avec ce qu'elles deviennent si vous gagnez ou perdez la prochaine manche.
  Calcul : chaque manche se gagne avec ton % de manches gagnées de ce côté sur la map, ramené vers 50 %.
- Les deux équipes : agents, rôles, niveaux visibles, ton groupe, et le compte par rôle.
- En bas : attaque et défense sur la map, ton agent ici, ta journée, ta série, ton rang.

Aucun pseudo n'est affiché. Le suivi manche par manche vient des changements de score du client Riot :
rien n'est lu dans la mémoire du jeu.

## Emblème Or

Quand tu es Or 1, 2 ou 3, le panneau « Rang compétitif » affiche un emblème dessiné exprès (pas celui de Riot) :
un hexagone doré pointe en haut et en bas, biseauté, avec ta division au centre, des rayons et un anneau
qui tournent lentement, un reflet qui passe dessus, et les 3 losanges de division en dessous. Les autres rangs gardent le losange.

## Écran de sélection d'agent (automatique)

Il s'ouvre tout seul quand la sélection d'agent commence, et se ferme quand la partie démarre.
Échap (ou le bouton « Dashboard ») bascule entre cet écran et le dashboard.
Même style que l'écran Stats :

- À gauche, sur toute la hauteur : l'agent que tu survoles ou que tu as verrouillé (artwork ASCII, sinon son image),
  avec ton % de victoires partout et sur cette map, ton K/D et ton ACS sur la map.
- En haut : la map en grand, un repère sur la map (pour les maps classiques), le côté de départ, le chrono,
  le nombre d'adversaires verrouillés, et les infos clés (ta place parmi tes maps, ton meilleur agent ici,
  les rôles qui manquent, ta série).
- Ton bilan sur la map : victoires, matchs, K/D, ACS, classement, attaque, défense, manches, ta moyenne.
- Ton équipe : les 5 agents (verrouillé ou en sélection), leur rôle, et le compte par rôle (en rouge s'il en manque).
- Tes meilleurs agents sur cette map (« conseillé » s'il comble un rôle manquant), tes derniers matchs ici,
  tes rôles sur cette map.
- Conseils tirés de ton historique : rôle manquant, ton meilleur agent ici, ton agent si ça se passe mal ici,
  la map pour toi, le côté de départ, ta série du moment.
- En bas : ta journée, ta série, tes 10 derniers matchs, ton rang et les RR qui manquent, solo ou en groupe.

Les stats viennent de la même base que l'écran Stats (HenrikDev + historique local) : compétition si la partie
est classée (tous modes s'il y a moins de 3 matchs classés sur la map). Aucun pseudo de joueur n'est affiché.

## Commentaires (touche M)

De petites alertes apparaissent en haut à gauche du dashboard et disparaissent seules, sans rien bloquer
(2 au plus à l'écran, 7 s d'écart minimum entre deux). Touche M pour les couper ou les remettre.

Chaque alerte s'affiche aussi en grand sur l'écran du boîtier, par-dessus ce qui y tourne (paroles,
image, audio…), le temps de l'alerte : `panel_alerts.py`, branché dans `launcher.py` sur l'envoi des frames,
et la route POST `/api/valorant/alert`. Il faut que la page du dashboard soit ouverte (c'est elle qui
décide des alertes). Police : Bahnschrift (Windows 10 et 11), sinon Arial gras.

- En partie : manche pistolet, séries de 3 ou 5 manches, mi-temps, balles de match, prolongation, remontada.
- Au verrouillage de l'agent : ton main, ton meilleur agent, la combo agent × map, ta meilleure ou pire map.
- Après le match : ace, clutch 1v3 ou plus, MVP, victoire en prolongation, raclée, partie difficile,
  séries de victoires ou de défaites.
- PC : GPU au-dessus de 83 °C, CPU au maximum, RAM presque pleine.
- Dans les menus, un « fun fact » tiré de tes stats toutes les 12 minutes.
