# Relecture d'un film Rive CLI — timeline scrubbable

Un artboard de relecture qui entoure n'importe quel film généré : le film en boucle en haut, une barre de 240 px en
dessous avec les **parties nommées**, les **graduations** (1 s, étiquettes toutes les 5 s), les **accents musicaux**
(losanges dorés), une **tête de lecture** qu'on clique ou qu'on **glisse**, un bouton **lecture / pause** (clic sur
l'image = pause aussi), un **compteur**, et la **bande-son** recalée à chaque saut. Premier usage : démos a client (a demo folder, 2026-09-28).

```
rive <projet> --artboard="<Nom> Review" --fit=contain      # le viewer : boucle, son, souris
```

## Fichiers

| | |
|---|---|
| `ReviewPlayer.luau` | le lecteur (ScriptedDrawable, protocole `Node`) : horloge, pause, scrub, audio, écrit le ViewModel |
| `ReviewNotes.luau` | la couche d'annotation (ScriptedLayout + `FocusData`) : dessin, saisie, repères, sortie `#ANN` |
| `review_timeline.py` | `review_rml(...)` → `(artboard_xml, roots_xml)` à coller dans la scène ; `install_player(projet)` copie les deux scripts (`REVIEW_NOTES=0` enlève la couche) |
| `review_notes.py` | côté CLI : `open` (viewer + ramassage), `watch` (depuis un log), `list` → `<projet>/.review/notes.json` (+ `notes.log`, le filet) |

## Contrat avec le film (à respecter dans le générateur)

1. **Le film est une fonction de SA timeline principale.** La relecture imbrique le film avec une
   `NestedRemapAnimation` dont le `time` (fraction 0..1) est lié à la propriété `progress` ; le script l'écrit à
   chaque frame. Tout ce qui doit suivre le scrub est donc keyé dans cette timeline, ou imbriqué par
   `NestedRemapAnimation` keyée **linéairement** dans cette timeline (actes, plans).
2. **Pas de `NestedStateMachine` pour les actes** : une machine imbriquée avance sur l'horloge réelle et ne suit ni
   la pause ni le scrub. Les personnages d'ambiance (boucles) peuvent rester en `NestedStateMachine` : ils vivent
   en pause aussi, c'est voulu.
3. **Un remap imbriqué est appliqué avec une image de retard (mesuré)** : un acte remappé au temps `f/DUR` montrait
   l'image `f-1` du film. Keyer `(0, 1/DUR)` → `(DUR-2, (DUR-1)/DUR)` rend le film identique au pixel à la version
   en machines d'état (écart moyen 0,02–0,14, résidu = phase des boucles d'ambiance).
4. Même durée pour toutes les timelines (film + actes), `loopValue="loop"` : le film joué seul boucle aussi en phase.

## Ce que le script lit / écrit

- Entrées (ScriptInput, par nom) : `duration` (s), `barX0/barX1` (bord de la barre), `barY0/barY1` (zone de clic de
  la barre), `btnX/btnY/btnR` (bouton), `filmBottom` (clic au-dessus = pause), `startPaused`, `audio` (nom de
  l'`AudioAsset`, vide = muet). `review_rml` les remplit.
- ViewModel `<Nom> VM` : `progress` (→ remap du film, clé 202), `headX` (→ x de la tête, clé 13), `timeText` (→ run du
  compteur, clé 268), `playOp` / `pauseOp` (→ opacité des icônes, clé 18).
- Pointeur : le script reçoit **tous** les événements de l'artboard (doc `luau/protocols`) et appelle `event:hit()`
  sur ceux qu'il traite (clic et chaque move du glisser).
- Audio : `context:audio(nom)` → `Audio.play` ; pause/reprise, `seek` au relâcher du scrub, relance à chaque boucle.
  `Audio.play` rend nil en headless (--screenshot / --data-dump) : le reste fonctionne, silencieux.

## Tester sans fenêtre

```
F='progress,timeText,headX,playOp,pauseOp'
rive . --artboard="X Review" --data-dump=- --data-dump-filter=$F --advance=5s                               # ≈ 5.0 s
rive . --artboard="X Review" --data-dump=- --data-dump-filter=$F --advance=1s --pointer=click@376,1584 --advance=0.5s   # saut à 30 %
rive . --artboard="X Review" --data-dump=- --data-dump-filter=$F --advance=1s --pointer=click@64,1500 --advance=2s     # pause : le temps ne bouge plus
rive . --artboard="X Review" --data-dump=- --data-dump-filter=$F --advance=1s '--pointer=drag@200,1584>800,1584:12'     # glisser
```
(coordonnées pour un film 1200×1460 : barre à y = H + 124, bouton à (64, H + 40)).

## Annoter (relecture façon Frame.io)

Un film de présentation de 63 s, monté sur le trailer de a game :
**[review_kit_demo.mp4](review_kit_demo.mp4)** `demo_grab.py` prend les captures (un run du
viewer par image, quatre copies du projet en parallèle : 80 images en 30 s), `demo_film.py` fait la
caméra et les cartons, ffmpeg monte. Les notes de la démo sont dans `demo_notes.json`.

La couche `ReviewNotes.luau` transforme la relecture en outil de retours : on **tape son commentaire
directement** (comme la boîte « Leave a comment » de Frame.io), on peut **dessiner sur l'image**, et
tout ressort côté CLI, daté à l'image près.

Sur un projet **qui n'a rien prévu** (un `scene.rml` écrit ailleurs, pas de gen_scene.py), tout se
pose en une commande — `review_install.py` lit la scène, y choisit le film et son animation, et
ajoute l'artboard de relecture entre deux marqueurs (voir [INSTALL.md](INSTALL.md), en anglais, pour
distribuer le kit) :

```bash
python3 <corpus>/review/review_install.py <projet>              # pose (ou remplace) la relecture
python3 <corpus>/review/review_install.py <projet> --refresh    # recharge les notes prises depuis
python3 <corpus>/review/review_install.py <projet> --remove     # rend le projet à son état d'avant
```

```bash
python3 <corpus>/review/review_notes.py open <projet> "DemoB Review"   # ouvre le viewer et ramasse
python3 <corpus>/review/review_notes.py list <projet>                  # relit <projet>/.review/notes.json
python3 <corpus>/review/review_notes.py list <projet> --new           # seulement les nouvelles
```

Pour qu'un **agent** (Claude Code, Codex…) suive la relecture pendant qu'elle a lieu : `open …
--background` rend la main tout de suite (le viewer continue, sa sortie va dans
`.review/session.out`), puis `status`, `list --new` entre deux corrections, et `stop` à la fin.

Dans la fenêtre :

| Geste | Effet |
|---|---|
| **taper**, ou **cliquer dans le champ** en bas de la colonne | ouvre un commentaire à l'image affichée et met en pause (accents compris) |
| **Entrée** ou le bouton **Send** | poste la note ; **Échap** annule ; **⌫** efface une lettre puis le dernier trait |
| **une des 4 couleurs**, ou le bouton **brosse** | prend la brosse (et ouvre le commentaire) ; on dessine sur l'image dans cette couleur |
| **espace** | lecture / pause · **← →** image par image (hors saisie) |
| **clic sur la barre** | aller là · **glisser** = scrubber |
| **clic sur un point** de la bande des commentaires | s'arrêter sur la note et la choisir dans le fil |
| **clic sur une ligne du fil** | **s'arrêter** sur son image (le dessin de la note y réapparaît) · **re-cliquer** = rouvrir pour corriger |
| **la coche / la croix** d'une ligne choisie | marquer traitée · **supprimer** la note |
| **clic ailleurs** (image, barre, panneau, colonne vide) | sort de la saisie : ce qui est écrit ou dessiné est posé, un brouillon vide est jeté |

L'interface du viewer est **en anglais** (`COMMENTS`, `Leave a comment…`, `Send`) ; le CLI et cette
doc restent en français.

La colonne tient en trois étages : l'en-tête **COMMENTS** avec le nombre de notes, le **fil**, et en
bas le **champ de commentaire** — un cadre visible en permanence (liseré discret, rouge quand on
écrit), le timecode de l'image en cours, l'invite, la ligne d'aide et une **barre d'outils** : quatre
couleurs (la choisie porte un anneau), la brosse, le bouton **Send** (éteint tant qu'il n'y a rien
à poster).

Le texte **revient à la ligne** et la boîte **grandit vers le haut** au fil de la frappe (le bas, la
barre d'outils et le bouton ne bougent pas) : le `Text` du champ est en largeur fixe + `wrap`, ancré
par le bas (`originY=1`), et le script écrit la hauteur et la position du fond dans le ViewModel.
Le curseur n'est plus dessiné à une position estimée — c'est **un `|` ajouté au texte lui-même**,
donc il suit le retour à la ligne et ne peut plus se décaler du texte.

Le fil liste les commentaires par timecode en pastille (`0:12.34`), **suit la tête de lecture** (la
note de l'instant se surligne et le fil défile), montre un petit trait quand la note porte un dessin
— dans **sa** couleur — et grise celles qui sont traitées ; la ligne choisie porte un liseré rouge et
ses deux actions (coche, croix). Sous les blocs, une **bande propre aux commentaires** porte une pastille par note, de sa couleur,
cerclée de blanc et grossie quand la tête est dessus — posée là et pas sur les blocs, sinon elle
mange leurs libellés. Un clic dessus s'y arrête et choisit la note. Le tracé réapparaît sur l'image à ± 0,25 s de son instant.

Ni avatar ni nom d'auteur : la relecture est à une voix (celle de the author), et l'agent lit le fichier.

Ce que l'agent récupère (`<projet>/.review/notes.json`) : `{id, t (s), text, color (1-4), pts, box, done, at}`.
`pts` est aplati `x1,y1,x2,y2,…` avec `-1,-1` entre deux traits, **en coordonnées de l'artboard de
relecture** — l'image du film y est à l'échelle 1 en (0,0), donc un point dessiné = le même point
dans le film. Les notes sont **rechargées au démarrage** : `review_rml(..., notes=load_notes(PROJ))`.

Rien ne se perd : chaque événement est d'abord ajouté à `<projet>/.review/notes.log` (append-only),
puis le JSON est réécrit de façon atomique. Si le JSON disparaît (synchro, fausse manip),
`review_notes.py rebuild <projet>` le reconstruit depuis le journal. Deux sessions qui repartent
au même numéro ne s'écrasent plus : même id **et** même instant = correction, même id à un autre
instant = nouvelle note renumérotée.

Sous le capot : le script imprime `#ANN {…}` / `#ANNDEL id` / `#ANNRES id 0|1` sur la sortie standard
du viewer (le seul canal d'un script), `review_notes.py` les lit. Le clavier n'arrive qu'à un
`ScriptedLayout` avec un `<FocusData/>` (mesuré : un `ScriptedDrawable` ne reçoit rien), d'où la
couche séparée ; elle parle au lecteur par le ViewModel (`annOn`, `seekTo`, `cmd`).

Tester sans fenêtre (les `--key` synthétiques ne produisent pas de texte, le reste marche) :

```bash
rive . --artboard="X Review" --screenshot=/tmp/x.png --viewport=1580x1764 --fit=none \
  --advance=1s --pointer=click@1010,1500 --advance=0.1s '--pointer=drag@420,620>820,760:10' \
  --advance=0.1s --key=enter:press --advance=0.3s | python3 .../review_notes.py watch .
```

## Pièges payés

- **Un curseur de saisie dessiné à une position estimée finit toujours décalé.** Un script n'a pas
  les métriques de la police : `x = début + #texte * 7,7` tombe juste sur un mot, faux sur un autre,
  et devient absurde dès que le texte revient à la ligne. Le mettre **dans la chaîne**
  (`texte .. "|"`, une frame sur deux) le rend exact : c'est le moteur de texte qui le place.
- **`autoWidth` ne revient jamais à la ligne** — le texte file hors du cadre. Il faut une largeur
  fixe + `wrapValue="wrap"` ; et pour qu'un champ posé en bas de colonne grandisse vers le HAUT,
  l'ancrer par le bas (`originY="1"`), le fond suivant par un bind de hauteur et de position.
- **Deux scripts qui se partagent le flux pointeur** : celui qui laisse la main doit la laisser sur
  **tous** les événements, `pointerUp` et `pointerExit` compris. Le lecteur ne bloquait que
  `pointerDown`/`pointerMove` : au relâcher d'un trait, son `release()` voyait `wasPlaying` et
  **relançait le film et la musique** — la note semblait « disparaître » (le tracé n'était affiché
  qu'à ± 0,25 s de son instant, et la tête de lecture s'en allait).
- **Une piste audio en pause n'est pas une piste arrêtée** : pendant une annotation le lecteur fait
  `stop()` (plus `pause()`), sinon un `play()` déclenché ailleurs empile une seconde piste et le son
  continue tout seul. Un saut (`seekTo`) coupe puis relance au bon endroit.
- **Un tracé n'appartient qu'à son image** : il ne s'affiche qu'à ± 0,25 s de l'instant de sa note
  (comme un calque d'annotation Frame.io). Pour que ça ne ressemble pas à un effacement, **aller à
  une note met la lecture en pause** sur son image (`seekTo` arrête le film, il ne le relance pas) :
  sans ça le tracé filait en une demi-seconde.
- **On ne rend jamais le pointeur au lecteur au milieu d'un clic.** La couche d'annotation est
  appelée **avant** le lecteur : si son `pointerDown` baisse `annOn`/`holdPlay` (fin de saisie,
  note postée), le lecteur reçoit ensuite *ce même clic* comme un clic ordinaire sur l'image —
  il bascule en lecture, la tête s'en va, et la note qu'on vient de poser a l'air de s'effacer.
  La baisse est donc retenue (`unlock`) et appliquée au `pointerUp`. Corollaire : `annotating()`
  côté lecteur doit regarder **écrire (`holdPlay`) autant que dessiner (`annOn`)**, et pendant la
  saisie le premier clic sur la barre ferme la note sans scrubber — le deuxième scrubbe.
- **Un clic dans la colonne ne veut pas dire « une ligne »** : le vide sous le fil doit sortir de la
  saisie comme n'importe quel clic ailleurs, sinon le champ reste allumé sans qu'on comprenne pourquoi.
- **Les ids repartent à 1 à chaque lancement du viewer** (le script ne sait rien des sessions
  précédentes tant que la scène n'a pas été régénérée avec `notes=load_notes(PROJ)`) : côté CLI,
  même id + même instant = correction, même id + autre instant = nouvelle note renumérotée.

## Limites connues

- Une seule brosse (quatre couleurs), pas de flèche ni de cadre, pas de réponses à une note.