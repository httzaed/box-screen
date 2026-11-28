# Refonte de phosphor_ui.html : tout sur un seul écran, sans scroll

Instructions pour l'IA qui va coder la refonte.

## 1. Objectif

`phosphor_ui.html` est le dashboard de contrôle de Box Screen. Il est servi par `control_server.py` sur `http://127.0.0.1:7420`.

Aujourd'hui, la page fait environ 3 260 px de haut à 1440 px de large, et il faut scroller. Le but est d'avoir toute l'interface visible d'un coup, sans scroll vertical ni horizontal.

- Écran cible : 1920 × 1080 en plein écran (F11).
- Elle doit aussi tenir dans une fenêtre de navigateur normale, soit environ 1920 × 950.
- Sous 1100 px de large, la page peut redevenir une colonne qui défile.

Il s'agit d'une refonte de mise en page (HTML et CSS). Ce n'est pas une réécriture : le JS existant doit continuer à marcher sans modification, sauf les deux exceptions de la partie 6.

## 2. Fichiers fournis

1. `phosphor_ui.html` : le vrai fichier à modifier, dans le dossier `box-screen`. Il contient un seul bloc `<style>` et un seul bloc `<script>`, d'environ 1 500 lignes en tout.
2. La maquette HTML que Hedi te donne : c'est la référence visuelle.
   - Elle est écrite dans un format de maquette, avec des balises `<x-dc>`, `<sc-for>`, `{{...}}` et une classe `Component`.
   - Ne copie pas ce format ni son script. Reprends seulement la disposition, les tailles, les couleurs et l'ordre des éléments.
   - Le dessin SVG du boîtier dans la maquette est un simple placeholder. Dans le vrai fichier, garde le rendu ASCII 3D existant (`#pc3dwrap` et `#pc3d`).
   - Les textes entre crochets, comme `[Titre du morceau]`, sont des exemples. Les vrais textes sont remplis par le JS.

## 3. Règles absolues

1. **Garder chaque `id` existant.** Le JS appelle `getElementById` sur ces id :
   `auto-indicator`, `beatdot`, `bpm`, `bright`, `cascade-state`, `cascade-track`, `case-speed`, `case-zoom`, `clock`, `conn`, `crt-toggle`, `decay`, `force-viz-btn`, `hostip`, `led-zone-summary`, `log`, `lyrics-artist`, `lyrics-current-line`, `lyrics-feedback`, `lyrics-flag-btn`, `lyrics-live-status`, `lyrics-next-line`, `lyrics-offset`, `lyrics-previous-line`, `lyrics-search-btn`, `lyrics-source-name`, `lyrics-title`, `mini`, `modename`, `music-routing`, `music-settings`, `pbar`, `pc3d`, `pc3dwrap`, `peak`, `play`, `ptime`, `screen`, `screen-mode`, `signal`, `trackname`, `uptime`, `vuL`, `vuR`.
   - Le bloc `<div hidden>` qui contient `#mini`, `#vuR`, `#peak`, `#pbar` et `#uptime` reste tel quel, caché.
   - Garde aussi les id des palettes : `palette-case`, `palette-fans`, `palette-fans2` et `palette-keyboard`.
2. **Garder les classes et attributs que le JS cible :**
   - `.color-swatch` avec `data-zone` et `data-color` ;
   - `.zone-btn` avec `data-zone` ;
   - `.led-mode-btn` avec `data-led-mode`, y compris le bouton `sync_fans`, qui a l'attribut `hidden` ;
   - `.palette` ;
   - `[data-view]` pour les 5 vues ;
   - `[data-case-option]` (`auto`, `explode`, `interior`) avec `role="switch"` et `aria-checked` ;
   - `[data-rhythm-key]` ;
   - la classe `active` (sélection) et la classe `crt` sur `body`.
3. **Garder les structures parent / enfant que le JS parcourt :**
   - Chaque curseur LED garde la forme `.slider-row > label + input[type=range] + .slider-val`. Le JS fait `input.closest('.slider-row').querySelector('.slider-val')`. Cela vaut pour `#bright`, `#speed`, `#decay` et `#lyrics-offset`.
   - Chaque curseur de rythme garde la forme `label.music-control > .music-label > output` avec l'input dans le même `.music-control`. Le JS fait `el.closest('.music-control').querySelector('output')`.
   - `#lyrics-feedback` doit contenir un `<span>`. Le JS fait `fb.querySelector('span')`. Si ce span n'existe pas déjà, ajoute un `<span></span>` vide.
   - Chaque `.palette` contient un élément `[data-zone]`. Le JS cache les palettes qui ne concernent pas la zone choisie.
   - `#decay` est dans une `.slider-row`. Le JS cache cette ligne quand la zone choisie n'est pas le boîtier.
4. **Garder les handlers inline** `onchange="changeRhythm(this)"` et `oninput="showRhythmValue(this)"` sur les contrôles de rythme.
5. **Ne rien changer à la logique JS**, aux routes `/api/...`, au SSE ni aux raccourcis clavier (F, ↑ et ↓, plus Ctrl+Shift).
6. **Pas de nouvelle dépendance.** Pas de framework, pas de CDN, pas de police externe : la police reste `'Box Terminal'` (Consolas). Tout reste dans un seul fichier.
7. **Garder l'identité visuelle actuelle :**
   - fond `#020803` ;
   - panneaux `#06150d` ;
   - bordures `#153723` ;
   - vert `#2bff88` ;
   - texte secondaire `#9ab4a4` ;
   - ambre `#ffc24d` ;
   - fond actif `#0e3a22`.

## 4. Nouvelle mise en page

Tout se passe dans `main.app`.

### Conteneur

```
body : margin 0, overflow hidden (au-dessus de 1100 px de large)
main.app : height 100vh, max-width none, padding 20px 24px,
           display flex, flex-direction column, gap 16px
```

- Sous `main.app` on trouve deux enfants : la barre du haut, puis une grille de 3 colonnes.
- La grille occupe toute la hauteur restante : `flex 1 1 auto; min-height 0`.
- Colonnes : `minmax(0,1.2fr) minmax(0,1fr) minmax(0,1fr)`, avec 16 px d'écart.
- Chaque colonne est un flex vertical avec 16 px d'écart et `min-height: 0`.
- Mets `min-height: 0` partout où un enfant flex doit pouvoir rétrécir.

### Barre du haut (environ 52 px, une seule ligne)

- À gauche : `TROFEO VISION` (h1, 26 px), puis le sous-titre « Box Screen Control · Écran 1920 × 462 » sur la même ligne.
- À droite : les pastilles `.services` (`#conn`, `#hostip`, `#clock`), puis le rappel des raccourcis « F : visualisation · ↑ ↓ : luminosité », puis le bouton Journal, puis `#crt-toggle`.
- **Supprime** la `<nav>` d'ancres : sans scroll, elle ne sert plus. Garde quand même `#crt-toggle` en le déplaçant dans la barre du haut.
- **Supprime** le `<footer>`. Son texte de raccourcis part dans la barre du haut.

### Colonne 1 : écran, paroles, affichage

1. **Panneau aperçu** (section `#overview`, hauteur fixe) :
   - `#screen` garde `aspect-ratio: 1920/462` et `width: 100%`. Il fait environ 165 px de haut à cette largeur.
   - Garde les badges `.preview-badges`, dont `#signal`.
   - Dessous, la bande « lecture en cours » sur une ligne, avec un padding de 14 px 18 px :
     - à gauche, l'eyebrow avec `#play`, puis `#trackname` en 20 px, coupé par une ellipse ;
     - à droite, les stats : Position `#ptime`, Tempo `#bpm` + `#beatdot`, Affichage `#modename` et Niveau `#vuL`.
   - **Supprime** le paragraphe `.footnote`.
2. **Panneau Paroles** (`flex: 1`, prend toute la hauteur restante) :
   - En-tête sur une ligne : h2 « Paroles », « Source : » `#lyrics-source-name`, puis `#lyrics-flag-btn` collé à droite.
   - `#lyrics-current` prend la hauteur restante : `flex 1; min-height 120px`. Son contenu est centré verticalement :
     - `#lyrics-live-status` ;
     - `#lyrics-previous-line` en 16 px ;
     - `#lyrics-current-line` en 30 px, couleur `#b5ffcf` ;
     - `#lyrics-next-line` en 16 px ;
     - `overflow: hidden`.
   - `#lyrics-feedback`, puis la ligne de recherche (`#lyrics-artist`, `#lyrics-title`, `#lyrics-search-btn`).
   - Puis la `.slider-row` de `#lyrics-offset`. Le paragraphe d'aide sous le décalage devient un attribut `title` sur le label.
3. **Panneau Écran** (hauteur fixe, environ 110 px) :
   - Une ligne : h2 « Écran », « Contenu affiché : » `#screen-mode`, puis « Visualisation forcée » et `#force-viz-btn` collés à droite.
   - Une ligne de texte : l'explication de la cascade sur une seule ligne courte, suivie de `#cascade-state` et `#cascade-track`.

### Colonne 2 : boîtier 3D, musique vers lumière

1. **Panneau Boîtier** (`flex: 1`, padding 16 px) :
   - En-tête : h2 « Boîtier », et à droite les 5 boutons `[data-view]` en ligne.
   - La scène `.case-stage` / `#pc3dwrap` prend toute la hauteur restante : `flex 1; min-height 200px`.
     - **Retire** la hauteur fixe de 500 px sur `#pc3dwrap`. Il y a plusieurs règles CSS qui la définissent, à 300, 420, 500, 390 et 350 px : neutralise-les toutes au-dessus de 1100 px.
     - `#pc3dwrap` doit faire `height: 100%`.
   - La légende « Thermalright · 8 ventilateurs RGB » et « Glisser pour tourner » se place en superposition, en bas de la scène.
   - Dessous :
     - les 3 interrupteurs `[data-case-option]` sous forme de petits boutons en ligne, avec les libellés courts « Rotation automatique », « Vue éclatée » et « Intérieur visible » (les sous-textes `<small>` peuvent passer en `title`) ;
     - une ligne avec les deux curseurs `#case-zoom` et `#case-speed`, côte à côte.
   - **Supprime** les deux blocs `.preview-palette`, qui contiennent `palette-case-preview` et `palette-fans-preview`. Ce sont des doublons des palettes de la colonne 3, avec les mêmes `data-zone` et le même effet.
   - Supprime aussi la phrase « Ces réglages modifient uniquement l'aperçu. »
   - L'`aside.case-controls` disparaît en tant que colonne. Son contenu passe sous la scène.
2. **Panneau Musique → lumière** (hauteur fixe) :
   - En-tête : h2 « Musique → lumière », avec le sous-titre court « Ce que les LED suivent ».
   - Les 3 `.music-mode` en ligne, sur 3 colonnes. Chaque bouton montre son `<strong>` et un `<small>` d'une ligne, coupé par une ellipse. Textes courts :
     - « Toutes les zones sur les kicks » ;
     - « Un rythme par zone » ;
     - « Anciens effets ».
     Le texte complet passe en `title`.
   - `#music-settings` contient sensibilité, intensité et extinction. Chaque curseur fait une ligne compacte : label à gauche (190 px), curseur, valeur `output` à droite. Les `<p>` d'aide passent en `title`.
   - Le curseur « Éclairage minimum du clavier » est raccourci en « Clavier minimum ». Il suit le même format et sort de son `div` avec `max-width: 420px`.
   - `#music-routing` : voir la partie 5, pour réserver sa place.

### Colonne 3 : Lumières

C'est un seul panneau, `section#lighting`, avec un padding de 18 px 20 px. Dans l'ordre :

1. h2 « Lumières ».
2. `.zone-group` : 3 boutons `.zone-btn` sur 3 colonnes. Leurs sous-textes deviennent « Périmètre », « Pales » et « Touches ».
3. La `.slider-row` de `#bright`. Le paragraphe « s'applique à toutes les zones » passe en `title`.
4. h3 « Effet lumineux », puis `.led-mode-grid` sur **3 colonnes**, avec des boutons d'au moins 44 px de haut. Le texte est coupé par une ellipse s'il est trop long.
5. Les `.slider-row` de `#speed` et `#decay`.
6. Un séparateur, puis `.palettes`. Pastilles `.color-swatch` de 32 px, espacées de 9 px. Rappel : le JS n'affiche qu'une ou deux palettes à la fois, selon la zone.
7. `#led-zone-summary`, en bas du panneau (`margin-top: auto`).

La section `.lighting` actuelle mélange l'aperçu 3D et les réglages. Il faut donc la scinder :

- l'aperçu 3D devient un nouveau panneau en colonne 2 ;
- les réglages restent dans `section#lighting` en colonne 3.

Les id et classes de la règle 3 doivent rester présents dans les deux.

### Journal

`details.journal` sort du flux de la page :

- Le bouton « Journal » de la barre du haut l'ouvre comme un panneau superposé (`position: fixed`), en bas à droite, d'environ 560 × 320 px, avec `#log` qui défile à l'intérieur.
- Il se ferme par le même bouton.
- Le plus simple : garder `<details>` et le positionner en `fixed`. Le `<summary>` sert alors de bouton.
- Autre solution : un bouton qui bascule l'attribut `open`. C'est la seule logique JS à ajouter (voir partie 6).

## 5. Points CSS importants

1. **Tailles en 1920 × 1080** :
   - texte courant 15 px ;
   - textes secondaires 13 à 14 px ;
   - h2 20 px ;
   - h1 26 px ;
   - boutons : padding 9 px 12 px, rayon 8 px ;
   - curseurs : piste 6 px, pouce 18 px ;
   - panneaux : rayon 12 px.
2. **Réserver la place de `#music-routing`** pour éviter que la page saute quand on change de mode. Le JS met `hidden` sur `#music-routing` hors du mode « Par zone ». La règle globale `[hidden]{display:none!important}` le fait alors disparaître. Ajoute à la place :
   ```css
   @media (min-width:1101px){
     #music-routing[hidden]{display:block!important;opacity:.45;pointer-events:none}
   }
   ```
   La place reste prise et les sélecteurs apparaissent grisés. Mets les 3 `select` sur une ligne de 3 colonnes. Le paragraphe « La séparation reste approximative… » passe en `title`.
3. **Mode Historique** : `#music-settings` est caché par le JS dans ce mode. C'est acceptable : le panneau rétrécit et ne déborde pas.
4. **Débordement** :
   - chaque panneau qui contient une zone flexible a `min-height: 0` ;
   - `overflow: hidden` sur `#lyrics-current` et sur la scène 3D ;
   - aucun scroll de page au-dessus de 1100 px de large.
5. **Budget de hauteur à 1080 px.** Zone utile de la grille : environ 970 px.
   - Colonne 3 : environ 860 px quand toutes les palettes sont visibles, moins en réalité puisque le JS en cache.
   - Colonne 2 : environ 380 px fixes pour Musique, le reste va au boîtier.
   - Colonne 1 : environ 240 px pour l'aperçu, 110 px pour l'écran, le reste aux paroles.
   - À 950 px de haut, seules les zones flexibles (paroles, scène 3D) rétrécissent. Si une colonne déborde encore, réduis les écarts (16 à 12 px) et les paddings avant de toucher aux tailles de texte.
6. **Sous 1100 px de large** :
   - `main.app` repasse en `height: auto` ;
   - la grille passe en une colonne ;
   - `body` redevient scrollable ;
   - `#pc3dwrap` reprend une hauteur fixe d'environ 420 px ;
   - `#music-routing[hidden]` redevient `display: none`.
7. **Nettoyage** : beaucoup de règles CSS actuelles se redéfinissent plusieurs fois. Elles sont empilées en fin de `<style>` : `.case-composition`, `#pc3dwrap`, `.light-top`, etc. Tu peux nettoyer le CSS des éléments déplacés. Ne touche pas aux règles du rendu ASCII : `.case-face pre`, `.asc`, `.fanx` et le commentaire « Preserve the exact cell geometry ».

## 6. Seules modifications JS autorisées

1. **Ajustement du boîtier 3D à la hauteur.** Dans le rendu du boîtier, il y a cette ligne :
   ```js
   const fit=Math.min(1,pc3dwrap.clientWidth/480);
   ```
   Elle ne tient compte que de la largeur, alors que le boîtier fait 462 px de haut. Remplace-la par :
   ```js
   const fit=Math.min(1,pc3dwrap.clientWidth/480,pc3dwrap.clientHeight/560);
   ```
2. **Bouton Journal** : si tu n'utilises pas le `<summary>` comme bouton, ajoute 3 lignes maximum pour basculer `details.journal.open`.

Rien d'autre. Si une autre modification JS te semble nécessaire, arrête-toi et explique pourquoi à Hedi avant de la faire.

## 7. Méthode de travail et vérifications

Dans ce dossier, des écritures entre Windows et une VM ont déjà tronqué ou rempli d'octets NUL des fichiers, dont `phosphor_ui.html`. Un script coupé suffit à tuer tout le JS de la page. Les vérifications ci-dessous sont donc obligatoires.

1. **Sauvegarde avant de commencer** : copie `phosphor_ui.html` vers `.refonte-onepage-before.html`. N'écrase pas `.refonte-before.html`, qui existe déjà.
2. **Relevé initial** : note la taille du fichier et la liste de tous les `id`, de tous les `data-*` et de toutes les classes ciblées par le JS.
3. Fais la refonte, puis écris le fichier en une seule fois.
4. **Intégrité du fichier écrit** :
   ```bash
   python -c "d=open('phosphor_ui.html','rb').read(); assert b'\x00' not in d, 'NUL'; assert d.rstrip().endswith(b'</html>'), 'fin'; print(len(d))"
   ```
   - Vérifie aussi que les dernières fonctions du script sont présentes : `connectSSE();`, `pollLiveLyrics();` et le dernier `setInterval`.
   - Extrais le `<script>` dans un fichier temporaire et lance `node --check` dessus.
5. **Comparaison avant / après** : chaque `id` de la règle 3 doit être présent exactement une fois. Les `data-zone`, `data-led-mode`, `data-view`, `data-case-option` et `data-rhythm-key` doivent tous être présents. Écris un petit script qui compare les deux listes.
6. **Test visuel** :
   - Lance `node .refonte-preview.cjs`. Il sert le fichier sur `http://127.0.0.1:7422` et relaie `/api` vers le serveur sur le port 7420.
   - Ouvre la page en 1920 × 1080, puis en 1920 × 950. Avec Playwright, vérifie que `document.documentElement.scrollHeight <= innerHeight` et `scrollWidth <= innerWidth` dans les deux cas.
   - Vérifie qu'il n'y a aucune erreur dans la console.
   - Prends une capture de chaque taille pour Hedi.
7. **Test fonctionnel rapide**, serveur lancé :
   - changer de zone ;
   - cliquer un effet ;
   - cliquer une couleur ;
   - bouger la luminosité ;
   - passer en mode « Par zone » : les sélecteurs doivent s'activer, sans que la page saute ;
   - activer « Visualisation forcée » ;
   - faire tourner le boîtier à la souris ;
   - activer « Effet CRT » ;
   - ouvrir le Journal ;
   - appuyer sur F et sur ↑ ↓.
8. **Git** : ne commite que si Hedi le demande. Dans ce cas :
   - ajoute les fichiers un par un, jamais `git add -A` ni `git add .` ;
   - message au format `[REP] refonte phosphor_ui une page sans scroll`.
