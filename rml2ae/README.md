# rml2ae — Rive CLI project → After Effects

> Projet indépendant — non affilié à Rive Inc., Adobe Inc. ni Figma Inc. Voir `THIRD_PARTY.md` (sources, licences, marques).

Convertisseur générique : un dossier projet Rive CLI (`rive.yaml`, `scene.rml`, assets) → un `.jsx` ExtendScript
qui reconstruit le projet dans After Effects (AE sauve lui-même le `.aep`). Un fichier éditeur passe d'abord par
`rive create <dir> --from-rev=fichier.rev`. Premier cas de test : un film de 9 plans (identique au pixel sur les textes,
formes, images, caméras, séquenceur ; seuls les scripts Luau manquent — phase 2).

```bash
cd ae-rml-rive-cli
.venv/bin/python -m rml2ae mon_projet                 # → mon_projet/build/rml2ae/mon_projet.jsx + .ae-report.md
.venv/bin/python -m rml2ae mon_projet --run           # exécute dans AE 2026 (AppleScript), sauve le .aep
.venv/bin/python -m rml2ae mon_projet --shots 5 9.5   # images AE vs `rive --screenshot`, planche côte à côte
# options : --out <dir> · --fps N (défaut : fps majoritaire des animations) · --main <Artboard> · --keep-project
```

Python avec PIL + fontTools (métriques des polices, découpe des meshes). `rml2ae/schema.json` = cache de
`rive schema --json` pour les 352 types (clés de propriétés → noms) ; régénérer si le CLI change.

## Organisation des comps (2026-10-02) — `--layout industry` (défaut) / `--layout raw`

Le projet AE suit l'organisation d'un motion designer, sans changer un pixel (AE industrie vs brut : 0 % de pixels > 8/255
sur walk-test, teaser-test, bounce-test, film-test) :

- **un calque de forme par groupe** : un `Node` dont le sous-arbre n'est que formes simples devient UN calque de forme
  (transformation et clés du nœud sur le calque) ; chaque sous-nœud / forme y est un **groupe vectoriel** avec sa
  propre transformation keyée (opacité de groupe native : plus d'expression d'opacité) ; des formes **voisines
  isolées** partagent un calque « Parent · A…B ». Restent à part : dégradés (Gradient Ramp au niveau du calque),
  formes plumées, modes de fusion, clips, liaisons de données internes, peau / os ;
- **pas de null inutile** : un nœud identité (aucune transformation, aucune clé, aucun clip / liaison) ne crée plus de
  null ; un nœud qui porte **une seule** feuille statique (forme / texte / image sans rotation ni échelle propre) est
  **replié** : la feuille prend l'animation et les liaisons du nœud, son décalage passe dans le point d'ancrage (exact) ;
- `ae pull` lit aussi les **groupes** (table `<projet>.groups.json` : chemin du groupe dans son calque → id Rive) et
  divise l'opacité d'un nœud replié par celle de sa feuille ; les clés réécrites à l'identique (position 2D d'un groupe,
  x constant, cuisson image par image) ne sont plus vues comme des retouches (comparaison des valeurs, pas des clés).

Mesures, avant → après : walk-test 56 nulls / 93 calques de forme → 15 / 14 (125 groupes) ; film-test 321 / 433 → 296 / 260 ;
bounce-test 30 / 56 → 25 / 49. Les nulls restants sont des contrôleurs animés (caméras, glitch RVB à 3 copies…).

**Plumage (Rive CLI 1.3 : `Feather` dans un `Fill`, `fillRule="clockwise"`)** → solide de la couleur du remplissage
découpé par un masque par tracé, **contour progressif = 1,25 × force** sans expansion (mesuré : 0,09 % d'écart moyen
sur ombre portée décalée, lueur et forme mobile) ; placé sous / sur la forme selon l'ordre des peintures ; plumage
intérieur ≈ masque (rapport). Le Flou gaussien AE ne convient pas ici (non rendu par `saveFrameToPng`).

## Correspondance (phase 1)

| RML | After Effects |
|---|---|
| `Artboard` + `Fill` | comp (dimensions, fond = solide) ; durée = animation jouée, ou le plus long contenu imbriqué |
| state machine : état d'entrée → `AnimationState` (+ chaîne exit-time) | la comp joue cette animation ; une chaîne → comp « séquence » de précomps `Artboard · Animation` |
| `NestedArtboard` | précomp (collapsée si l'artboard ne clippe pas) ; `NestedRemapAnimation.time` (0..1) → Time Remap keyé ; `NestedSimpleAnimation` → précomp jouée |
| `Node` (x, y, rotation rad, scale, opacity) | null ; enfants parentés ; opacité propagée par expression (`value * parent.opacity/100`) |
| `Node` + `ClippingShape` | précomp du sous-arbre (dans l'espace du parent, canevas 3× pour les nœuds profonds) + copie du clip en track matte alpha |
| `Shape` + `PointsPath` (Straight / CubicDetached / Mirrored / Asymmetric), `Rectangle`, `Ellipse`, `Polygon`, `Star`, `Triangle` | calque forme, un groupe par Shape ; sommets keyés → clés de tracé |
| `Fill` / `Stroke` + `SolidColor` (couleur keyée), cap/join/thickness, `fillRule`, `TrimPath`, `DashPath` | Fill / Stroke / Trim Paths / Dashes ; gradients → couleur du 1er stop (≈, non scriptable) |
| `Image` (+ `Mesh` régulier keyé) | calque image (ancre = origine) ; mesh → une bande par rangée + Quatre coins |
| `Text` + `TextStylePaint` + `TextValueRun` + `FontAsset` | calque texte (police PostScript lue dans le TTF, taille, interligne, tracking, justification, boîte de paragraphe) |
| `TextModifierGroup` + `TextModifierRange` | animateur de texte (Position/Rotation/Opacité/Échelle) + sélecteur (unités, index ou %, Start/End), `strength` → Amount |
| `LinearAnimation` (hold / linear / cubic `CubicEaseInterpolator`) | clés AE ; ease CSS → `KeyframeEase(speed, influence)` sur les deux clés du segment ; autres interpolateurs → linéaire (≈) |
| `blendModeValue` | mode de fusion (noms ou valeurs numériques) |
| `ScriptedDrawable` avec `ScriptInputArtboard` | l'artboard d'entrée posé en précomp (le rendu du script n'est pas appliqué) |
| `LayoutComponent`, os/skins, Luau, WGSL, data binding | non convertis (rapport) — phases 2-3 |

## Phase 2 (livrée)

| RML | After Effects |
|---|---|
| `Fill` d'artboard lié (`isVisible`/`opacity`) | solide « Background » avec expression (depuis 2026-09-17 soir) |
| `ViewModel` + instance par défaut | comp « VM · <nom> » : un null `ViewModel` avec Slider (nombre), Checkbox (booléen), Color Control (couleur), calque texte guide (chaîne) — valeurs = instance par défaut |
| `DataBindContext` (sourcePathIds → propertyKey) | expression sur la propriété AE (x, y, rotation, échelle, opacité, isVisible, couleur de fill/stroke, épaisseur, trim, texte source, strength d'animateur) ; converters et chemins imbriqués → valeur brute + note |
| `ScriptedDrawable` / `ScriptedLayout` sans artboard d'entrée | **rejeu par le CLI** : copie temporaire du projet ne gardant que l'élément et ses ancêtres, deux rendus (fond noir / blanc) dématricés en alpha droit, séquence PNG importée à la cadence de la comp, posée en espace monde (`--no-replay` pour désactiver ; cache dans `build/rml2ae/replay/`) |
| script avec `ScriptInputArtboard` + `context:shader("x")` | shader `x.wgsl` reconnu par les champs de `struct Params` (grain / wobble / displace / vignette / tick) → comp « <artboard> + FX » (Noise, wiggle(), Turbulent Displace, vignette masquée) puis « + FX (stepped) » avec Posterize Time si le script pas son horloge ; valeurs lues dans le ViewModel puis dans les `num(vm, "x", défaut)` du Luau |
| state machine à entrées/événements, blend states | non convertis : notes dans le rapport (l'animation de l'état d'entrée est jouée) |

## Phase 3 — plugin After Effects « Rive Shader » (livré)

`rml2ae/plugin/` : effet SmartFX (`RIVE RiveShader`) qui exécute un `.wgsl` Rive tel quel sur un calque par wgpu-native
(Metal) — 8/16/32 bpc, `struct Params` → sliders / couleurs / points renommés d'après le shader, textures supplémentaires →
paramètres Layer, horloges `tick`/`fxTick`, erreurs sans modale. Validé dans AE 2026 au pixel contre l'oracle
`rml2ae/wgsl_apply.py` (prototype wgpu-py), mémoire plate sur 5000 rendus `aerender`. Voir `plugin/README.md` (convention,
index des paramètres, tests) et `PHASE3_AE_PLUGIN_BRIEF.md` (journal, pièges).

`script_fx` émet l'effet quand le plugin est installé (`app.effects` contient `RIVE RiveShader`) : `rml2ae/riveshader.py`
lit `struct Params` + les `buffer.writef32(b, offset, num(vm, "x", d))` du Luau pour retrouver quelle propriété de ViewModel
alimente chaque champ, enregistre le shader dans `~/Library/Application Support/RiveShader/shaders.tsv`, et pose la comp
« + FX (stepped) » (Posterize Time = le pas d'animation Rive) ; sans plugin, la reconnaissance heuristique (Noise, wiggle,
Turbulent Displace, vignette) reste. Les textures rendues par une autre passe du script (une texture de passe = le même artboard instancié avec
`textVis=1, photoVis=0, bgVis=false`) se déclarent dans `ae_passes.json` à côté de `scene.rml` (par élément scripté :
surcharges de ViewModel pour `source` et pour chaque texture) ; rml2ae clone alors l'arbre de comps par passe avec une copie
« VM · <nom> [passe] » et retargete les expressions de binding (`cloneTree` / `cloneVm` dans le prélude JSX).

## Constantes mesurées (AE 2026, Montserrat)

Rive : première ligne de base = haut de boîte + ascender(hhea)·taille ; hauteur des bounds d'une ligne = 1,164·taille.
AE : `addBoxText` centre la boîte sur l'origine du calque ; première ligne de base = haut de boîte + 0,743·taille
(`AE_BOX_BASELINE`). Ordre des clés : BEZIER partout → eases → HOLD en dernier. `layer.parent = X` réécrit TOUTE la
transformation de l'enfant (réécrire position, rotation, échelle après). Un null est un solide à opacité 0. Une
précomp collapsée avec track matte est rasterisée à la taille de sa comp. Voir `RIVE_FILM_GUIDELINES.md` §5.

## Le bouton dans After Effects — `panel/Rive.jsx`

Panneau ScriptUI ancrable (installer une fois : `sudo cp rml2ae/panel/Rive.jsx "/Applications/Adobe After Effects 2026/Scripts/ScriptUI Panels/"`,
puis Fenêtre › Rive.jsx). « Update from RML → this project » génère le script par Python (`system.callSystem`) et
l'exécute dans AE (`$.evalFile`) : l'import précédent est remplacé, ~20 s pour tout le film. Boutons « Rive viewer »
(Terminal, `rive .`) et « Report ». C'est l'interface prévue pour la boucle de travail ; le CLI ci-dessous est le
même moteur en ligne de commande (rendus, diff, watch).

## `ae` — ligne de commande façon CLI Rive (bin/ae)

```bash
bin/ae doctor mon_projet          # AE ouvert ? aerender ? rive ? plugin Rive Shader ? polices ? deps ?
bin/ae templates                    # modèles de rendu / de sortie de l'AE ouvert (noms localisés : « Paramètres optimaux », « PNG »)
bin/ae build mon_projet           # nouveau projet AE + .aep ; --replace : reconstruit DANS le projet ouvert (remplace l'import précédent)
bin/ae watch mon_projet           # rebuild --replace à chaque sauvegarde de scene.rml / assets (20 s pour un film de 9 plans)
bin/ae screenshot mon_projet --advance 9.52
bin/ae render mon_projet --range 200-260 --out film.mp4   # aerender → PNG → ffmpeg (13 s de lancement + ~1,5 s/image)
bin/ae diff mon_projet --times 5 9.5 23.2                 # AE vs rive --screenshot
bin/ae pull mon_projet [--dry]                             # retouches AE (transformations, clés, masqué) → scene.rml
```

Le même `scene.rml` sert Rive et AE : la boucle rapide reste le viewer Rive (`rive <projet>`, temps réel), AE est vérifié
aux jalons. En mode `--replace`/`watch`, le rendu lit le projet AE **ouvert** (il doit être sauvé). Il n'y a pas de viewer
AE temps réel hors d'AE : `render`/`screenshot` sont des rendus.

**Incrémental par calque (livré, v2)** : chaque comp porte un tag (`comment` = `rive:<artboard>|<animation>`, `…|seq`,
`…|looped`, `…|clip`, `vm:<id>`) et chaque calque l'id Rive de son élément (`rive:0:287` sur le calque principal,
`rive:0:287+…` sur ses calques auxiliaires — dégradé PNG, matte — , `head+` sur le fond de la comp). Le script généré est
découpé en blocs (en-tête de comp / élément / élément enfant) dont l'empreinte est écrite dans
`build/rml2ae/<nom>.manifest.json` (par AE, en fin de script réussi, à chaque build). `ae build --replace` / `watch` /
le bouton du panneau comparent avec l'empreinte précédente :

- élément inchangé et son calque encore là → conservé (vos effets, masques, retouches sur ce calque survivent) ; ses
  enfants sont examinés à leur tour ;
- élément modifié (ou calque supprimé par vous) → son ancien sous-arbre de calques est retiré et recréé **à la même
  place dans la pile** (sinon au-dessus de son frère précédent, sinon au bas de son parent) ; ses enfants sont
  recréés avec lui, le parentage et l'expression d'opacité héritée sont refaits ;
- élément ajouté → créé à sa place dans l'ordre de dessin ; élément disparu du RML → ses calques retirés ;
- en-tête de comp modifié (taille, durée, fond) → la comp est refaite entière, les calques qui l'utilisaient sont
  relinkés (`replaceSource`, leurs retouches restent) ; comp disparue → supprimée ; médias déjà importés réutilisés ;
- vos calques ajoutés dans une comp conservée (sans commentaire `rive:`) ne sont jamais touchés.

Une clé qui change dans l'état A change les valeurs reportées dans l'état B : les éléments concernés de B sont refaits
aussi (c'est attendu). `--full` refait tout. Test à sec sans AE : `python3 -m rml2ae.tests.sim_incremental` (rien
modifié → rien refait ; couleur dans un groupe → la forme seule ; enfant de Solo retiré ; élément ajouté ancré ; fond
d'artboard → comps entières relinkées). Vérifié dans AE (2026-09-17, projet `features`) : flou gaussien ajouté sur le
calque « A » et solide « MY EDIT » ajouté dans la comp → couleur de « B » changée dans le RML : « B » seul recréé au même
index, « A » (même id de calque, flou intact), « Group », « Counter » et le solide conservés ; puis élément ajouté avant
« Group » → créé juste au-dessus, enfant « S2 » retiré du Solo → Solo refait sans lui, tout le reste conservé ; puis RML
d'origine restauré → « S2 » revient entre « S1 » et « S3 », l'élément ajouté disparaît, le flou et le solide toujours là.

**`ae pull` (livré)** : ce que tu as modifié dans le projet AE ouvert revient dans `scene.rml`. Portée v1 : la
transformation de chaque calque tagué `rive:<id>` — x, y, rotation, scaleX, scaleY, opacité, drapeau masqué (œil) —
en attribut quand la propriété n'a pas de clés dans AE, en clés de l'animation de la comp sinon (eases AE → 
`CubicEaseInterpolator` par inversion exacte de la correspondance de `build`, linéaire/hold conservés). Les valeurs
statiques sont lues dans la comp de la première animation de l'artboard ; les clés dans chaque comp d'animation ;
une clé unique sur une propriété non keyée dans le RML = valeur reportée d'un état précédent, ignorée ; calques
mattes, `[skinned]`, précomps clipées et auxiliaires `+` ignorés. Le RML est réécrit en texte (attributs et blocs
`<KeyedProperty>` seulement), vérifié par `rive --verify`, et le manifeste est marqué à jour pour ces éléments : le
`build --replace` suivant ne les reconstruit pas (leurs effets AE restent). `ae pull --dry` liste sans écrire ;
bouton « Pull this project's edits → RML » dans le panneau. Vérifié dans AE (2026-09-17) : x d'un groupe, 3 clés de
rotation avec easy ease + hold, deux drapeaux masqué/affiché → RML exact, `--verify` 0 erreur, build incrémental
suivant : 0 élément refait. Hors portée (v2) : couleurs/traits/chemins/texte modifiés dans AE, clés hors image
(arrondies à l'image de l'animation), calques ajoutés dans AE (ils restent dans AE, jamais dans le RML).

**Installer / distribuer** : `rml2ae/make_dist.sh` → `dist/rml2ae-<date>.zip` (package + `RiveShader.plugin` s'il est
construit + `docs/` avec tous les guides) ; dans le zip, `bash rml2ae/install.sh` fait tout (venv Python, rive CLI,
commande `ae`, panneau AE, plugin, skill Claude Code, `ae doctor`). Côté agent : `LLM_SETUP.md` (skill `skill/rml2ae`,
CLAUDE.md du projet, autres agents).

## Effets AE par commentaires RML (livré, chantier 2 de la v2)

Rive n'a pas d'effets (ombres, flous). Ils se déclarent dans le RML en commentaires, juste avant l'élément qui les
reçoit ; Rive les ignore (`--verify` les accepte), rml2ae les pose en effets AE natifs sur le calque principal :
```xml
<!-- ae: DropShadow x=2 y=6 blur=12 color=#33000000 -->      (AARRGGBB ; ou color=#000000 opacity=0.2)
<!-- ae: GaussianBlur radius=8 -->
<!-- ae: Glow color=#FFFFFF radius=20 intensity=1 -->  <!-- ae: Tint black=#000000 white=#FFFFFF amount=100 -->
<!-- ae: effect "ADBE Gaussian Blur 2" 0001=20 0002=1 -->    (brut : n'importe quel matchName + ses paramètres)
<Shape …>
```
Plusieurs commentaires s'empilent. `figma2rml` les écrit depuis les effets Figma (ombres multiples « smooth shadow »
fusionnées en une — des Drop Shadow AE empilés se composent l'un sur l'autre, mesuré). Mesuré dans AE 2026 : opacité du
Drop Shadow en 0..255, direction en degrés horaires depuis le haut, lissage ≈ rayon de flou Figma, alpha ×0,8 pour
retrouver la densité de Figma ; flou gaussien AE ≈ rayon Figma (×2 fait disparaître une bande de 200 px) ; ombre
interne : pas d'effet natif (styles de calque non scriptables) → signalée, pas posée. Bibliothèque : `rml2ae/effects.py`.

Corrections trouvées avec un board Figma vectoriel (mesurées dans AE) : les points d'un Gradient Ramp sont en coordonnées
de COMP, insensibles au parent → Ramp seulement sur une forme non parentée/non tournée, sinon dégradé PNG matté (aussi
pour tout dégradé avec alpha : le Ramp n'a pas d'alpha) ; Rive dessine la DERNIÈRE peinture d'une forme au-dessus, AE
l'item le plus HAUT du groupe → peintures ajoutées en ordre inverse ; une source de clipping sans peinture (Rive clippe
par la géométrie) reçoit un fill blanc pour servir de matte ; un JPEG nommé .png n'est pas importable par AE.
Résultat sur un plan de ce board : Rive 2,3 % / AE 2,4 % de différence pixel avec le rendu Figma.

## `ae2rml` — le sens inverse : projet After Effects → projet Rive CLI (en cours, 2026-09-29)

`rml2ae/ae2rml/` lit le `.aep` avec **py-aep** (MIT, `pip install py-aep`, lecture binaire hors AE — After Effects n'a
pas besoin d'être ouvert) et écrit `rive.yaml` + `scene.rml` + `assets/`. Aucune piste Lottie : le RML est produit
directement.

```bash
bin/ae import projet.aep mon_projet_rive [--comp X] [--fps N] [--once] [--no-bg] [--verify] [--shot 1 2.5]
                                         [--media-scale 0.5] [--media-fps 12] [--ae-lang fr] [--no-audio] [--no-video]
.venv/bin/python -m rml2ae.ae2rml projet.aep mon_projet_rive --verify        # même chose
```

Sorties dans `build/ae2rml/` : `report.md` (converti / approché / non converti, polices, footage), `effects_todo.json`
(effets sans équivalent Rive, avec paramètres : la liste de travail pour écrire les WGSL ou les faire écrire par l'IA),
`expressions_failed.json`, `idmap.json` (mêmes ids à la réimportation : diff lisible, tags AE stables) et
`tag_ae_project.jsx` (bidirectionnel, ci-dessous), et `bake_expressions.jsx` quand des expressions demandent AE
(voir « expressions »). `--no-audio` : aucun son (ni calques son, ni son des vidéos). `--no-video` : les fichiers vidéo (.mov, .mp4…) sont laissés
de côté, leurs calques ne dessinent rien (séquences d'images et images fixes restent). `--media-scale` réduit aussi les
**images fixes**, et une image opaque est écrite en JPEG (une photo en PNG RGBA pesait 10 Mo : broadcast-test, 1,06 Go de .riv).

| After Effects | Rive |
|---|---|
| comp principale (la plus grosse racine, ou `--comp`) + précomps atteintes | un artboard par comp (précomp = composant, clip sauf collapse) ; `LinearAnimation` au fps de la comp (loop, `--once`), state machine d'entrée |
| calque précomp | `NestedArtboard` + `NestedRemapAnimation.time` keyé (start time, stretch, time remap : exact) |
| transform de calque | `Node` position/rotation/échelle (le nœud que `ae pull` relit) + `Node` −anchor ; opacité et fenêtre in/out sur un nœud contenu (clés hold) |
| parentage | hiérarchie Rive ; si des enfants ne sont pas contigus dans la pile AE, le parent est **répété** (nœud fantôme, mêmes clés) : l'ordre de dessin reste exactement celui d'AE |
| calque de forme | groupes → `Node` (+ ancre) ; rectangle/ellipse → `Rectangle`/`Ellipse` ; chemin, étoile/polygone (formules d'AE) → `PointsPath` (`CubicDetachedVertex`, sommets keyés) ; Fill/Stroke/dégradés → peintures (l'ordre inversé dans une Shape) ; tirets → `DashPath` (décalage de signe inversé : AE recule le motif, Rive l'avance), et après un trim ou un effet de chemin → Luau `ae_dash` (le `DashPath` de Rive avale des espaces après un `TrimPath`), posé depuis l'autre bout quand Début > Fin sur un tracé fermé ou à plusieurs segments (AE parcourt alors le morceau à l'envers ; une ligne d'un segment garde son sens — 9 sondes AE + Arcs, Blowups, Complicated_Elements) ; Trim Paths → `TrimPath` dans la peinture (trait **et** remplissage, trims empilés enchaînés) ; Round Corners → `radius` des sommets droits ; Repeater → copies (transformations composées k fois, opacité début/fin, copies animées par clés hold) |
| sémantique AE des peintures | une peinture dessine tous les chemins AU-DESSUS d'elle dans son groupe (y compris ceux des sous-groupes, à travers leurs transformations, animées comprises : clés aux temps AE ou échantillonnage) ; une opération (trim) s'applique à tous les chemins au-dessus, donc aussi aux peintures du dessus ; Merge Paths : les peintures au-dessus sont ignorées (comme AE, qui ajoute un Fill dessous), soustraction → `fillRule=clockwise` + `isHole` (un seul tracé actif → rien à soustraire ; tracé de base antihoraire → `evenOdd`, sinon la règle horaire ne remplit rien : le crâne de broadcast-test n'avait que son contour), exclusion → `evenOdd`, union → normal, **intersection** → le 1er tracé découpé par un `ClippingShape` par tracé suivant (IoU 0,997) ; **inclinaison de groupe** → rotation · échelle · rotation exacte sur deux nœuds (axe d'inclinaison de sens opposé à la rotation, mesuré) ; **pointe (taper)** → le contour devient un Fill avec `ae_taper` (épaisseur W·fs·fe, accélération = bézier (0,0)(0,e)(1−e,1)(1,1), mesurée à 0,64 px) ; **couleurs de dégradé animées** → arrêts à l'union des positions, couleurs keyées — banc `~/.cache/ae2rml_tests/shapes/` |
| effets via le plumage Rive 1.3 | **Flou gaussien / Flou rapide (box) / Flou de zone** (calques forme / texte) → `Feather` sur chaque peinture du calque, clés si le flou est animé (mesuré AE 2026 : σ = 0,283 × flou ; box σ = r·√(n(1+1/r)/3), 1 passe 0,34·(2r+1)) — 0,02–0,16 % (box 1 passe 0,55 %) ; avec « Recopier les pixels du contour » (coché PAR DÉFAUT dans AE 2026) le contour du calque reste net dans AE → pas de plumage, signalé ; **Ombre portée** (calques forme / texte) → copie des dessins sous le calque, peintures à la couleur d'ombre + `Feather` (adoucissement × 0,43 ; fill en règle horaire), décalée dans l'espace du calque (direction depuis le haut, sens horaire ; défauts AE pour les paramètres non stockés : 135°, 5 px, 50 %) — 0,13–0,15 % ; **Lueur** → copie plumée PAR-DESSUS en mode écran (Rive n'a pas d'addition), couleur par canal (c − seuil)/(1 − seuil), rayon × 0,8 — halo à 0,15–0,18 % ; **Posterize Time** → toutes les clés du calque (transformation comprise, mesuré) tenues à sa cadence ; le temps propre d'une précomp n'est PAS posterisé (Shape_01 : 0,01 % contre 0,9 %) |
| Zig Zag, Offset Paths, Pucker & Bloat, Twist, Wiggle Paths | `ScriptedPathEffect` Luau (`ae2rml/luau/`, copiés dans le projet) posé **dans chaque peinture**, paramètres AE en `ScriptInputNumber` keyés ; Zig Zag / Offset / Pucker = algorithme de lottie-web (MIT), **identiques au rendu AE 26.5** (≤ 0,6 % de pixels, anticrénelage) ; Twist **mesuré dans AE** : chaque tracé tourne autour du centre de SA boîte englobante, angle·(1 − d/R), identique (≤ 0,4 %) ; Wiggle Paths (match name `ADBE Vector Filter - Roughen`) : Détail = points ajoutés par segment, poussés sur la normale de ±Taille/2 — même structure, tirages aléatoires différents |
| clés | hold / linéaire / bézier : influence + vitesse → `CubicEaseInterpolator` exact (vitesse le long du chemin pour les propriétés spatiales) ; bosse entre deux valeurs égales → `CubicValueInterpolator` ; trajectoire courbe, clés avant 0, stretch négatif → échantillonné puis réduit (`motion/reduce_keys.py` pour les transformations, Douglas-Peucker pour chemins et couleurs) ; chemins et couleurs : ease de progression mesurée sur l'interpolation py-aep ; **clés entre deux images** (projets étirés ou copiés depuis un autre fps) : replacées sur les images voisines en coupant la courbe exactement — chaque image montre la valeur d'AE (à 1e-4) au lieu d'un décalage d'arrondi ; **passe finale sans perte** (`rml.prune`) : clé entre deux valeurs égales sur un segment plat, maintien répété, clé linéaire sur la droite de ses voisines, dernière clé répétée, piste constante réduite à une clé — 1 820 clés retirées sur les 208 projets du stock, 0 pixel changé (balayage de 101 images) ; **pistes échantillonnées** (expressions, trajectoires) : ajustées en **courbes** (`keys.fit_curves` : segments gloutons, x1/x2 sur une grille, y1/y2 aux moindres carrés, tolérance 0,15 % de l'amplitude) au lieu d'une clé linéaire par image — Mh_LST_Resp_Drinking : 1 618 → 1 255 clés, fidélité inchangée ; l'inversion x(s) = u et les termes des moindres carrés ne dépendent que de la longueur du segment → mis en cache (14× plus rapide, résultat identique), et les pistes en **marches** (majorité d'échantillons égaux : masques image par image, sommets garés) ne passent plus par l'ajusteur — broadcast-test (400 000 clés, 1 422 masques) prenait 37 min |
| expressions | évaluées hors AE par un interpréteur JavaScript maison (`aexpr.py`) + les globals AE (thisComp, layer(), effect()(), content(), valueAtTime, loopOut, linear/ease, wiggle déterministe, sourceRectAtTime, toComp…) ; indépendante du temps → valeur statique, sinon échantillonnée ; littéraux regex, déstructuration `const {a, b} = …`, décomposition `...x`, `sourceText.getStyleAt()`, `fromCompToSurface`/`toCompVec`, `sourceRectAtTime` d'un texte = boîte d'encre des glyphes (fontTools ; mesuré contre AE : haut et hauteur au pixel, largeur +3,5 % quand AE applique son crénage optique), une propriété lue dans sa propre expression = sa valeur pré-expression (comme AE), une expression **en échec** donne sa valeur pré-expression à qui la lit (AE la désactive), `var` à portée de fonction et hissée, `this` / `new F()`, une **liste déroulante** remplie par expression est bornée à 1..n choix comme dans AE (n lu à l'octet 48+12 du `pard`), `eval(String.fromCharCode(…))` (code caché des presets de rig, exécuté dans la portée de l'appelant), `Array.from` / `isArray` / `of`, `some` / `every` / `find` / `findIndex` / `fill` / `flat` / `flatMap` / `at` / `reduceRight`, `path.pointOnPath` / `tangentOnPath` / `normalOnPath` (longueur d'arc ; la tulipe de broadcast-test suit ainsi son tracé), `layer("ADBE …")` / `group("ADBE Vectors Group")` par match name, noms et types des paramètres de **pseudo-effets** relus dans les définitions d'effet du .aep (py-aep ne les connaît pas) ; échec → valeur pré-expression + `expressions_failed.json` + **`bake_expressions.jsx`** : lancé dans AE (lecture seule), il relève image par image les valeurs de ces expressions (JSXBIN compilé, API manquante) dans `bake.json`, que la conversion suivante utilise — y sont aussi envoyées les expressions **évaluées mais inexactes hors AE** : `random()` / `gaussRandom()` / `wiggle()` / `noise()` (le générateur d'AE n'est pas public ; un tirage `seedRandom(s, true)` indépendant du temps n'est relevé qu'une fois). rig-test : 24 influences Randomatic de la caméra 2D Duik (643 → 29,1 dans AE) ; langue d'AE émulée (`--ae-lang fr|en|any`, défaut : langue du Mac) : un AE français ne résout `effect("X")("Slider")` qu'en français (`"Curseur"`), sinon il **désactive** l'expression — ae2rml fait pareil pour rendre ce qu'AE rend |
| solide / image / PSD | `Rectangle` / `ImageAsset` (PNG, JPEG si opaque) ; **calque de PSD** (AE importe un PSD calque par calque : un footage par calque) → ce calque seul, lu par un lecteur PSD maison (`util.psd_layers` : enregistrements de calques, RLE / ZIP, 8 et 16 bits, retrouvé par nom + bornes des `file_attributes` de py-aep) — PIL ne lit que l'image aplatie : les pictos de broadcast-test sortaient tous en affiche entière ; PSD sans calque → aplati ; `.ai` / `.eps` / `.pdf` rasterisés par `sips` (un `.ai` compatible PDF est copié en `.pdf`, sinon sips refuse) ; chemins Windows retrouvés par la fin du chemin |
| vidéo, séquence d'images | **séquence d'images** : seules les images affichées sont extraites (ffmpeg / fichiers numérotés), JPEG sans alpha, PNG avec ; un `Solo` dont l'enfant actif est keyé image par image (`KeyFrameId`, clé 296) ; start time, stretch, time remap, boucles ; effet couleur animé cuit **par image affichée** ; `--media-scale` / `--media-fps` pour alléger le .riv |
| texte | `Text` + `TextStylePaint` (police cherchée par nom PostScript sur le Mac, Adobe Fonts compris ; sinon **Helvetica**, la remplaçante qu'AE affiche sur ce Mac — mesuré : 0,85 de recouvrement des glyphes contre 0,79 pour Arial — + rapport) ; texte ponctuel : `originValue=baseline` ; paragraphe : 1re ligne à 0,743 × taille (constante mesurée par rml2ae) ; Source Text keyé → `KeyFrameString` ; animateurs (position, échelle, rotation, opacité) + sélecteur de plage → `TextModifierGroup` + `TextModifierRange` (couverture partielle d'AE rendue par des rampes d'une unité) ; **sélecteur d'expression** → une plage par unité (index), sa force keyée image par image depuis l'expression évaluée avec `textIndex`/`textTotal` ; **texte sur tracé** → `TextFollowPathModifier` sur une forme non peinte (Rive ignore la position du Text, décalage = fraction de longueur, groupe +ascendante déclaré APRÈS pour poser la ligne de base ; centrage calculé : milieu + Première − Dernière marge entière, mesuré) ; **approche animée** (1 px/unité, avant/après, Ancrage de lignes) → translation par caractère ; **lissage haut/bas, ordre aléatoire, sélecteur Tremblement** → forces cuites par caractère (`textsel.py`, formules lottie-web ; aléatoire et tremblement ≈, générateur AE non public) ; **couleur de fond/contour par plage, décalage de caractère** → un run par caractère + couleur / caractère keyés (emplacement d'origine conservé, crénage compensé) ; largeurs mesurées par HarfBuzz (crénage) — banc `~/.cache/ae2rml_tests/text/` |
| masques | `ClippingShape` : add (et éclaircir) = union, subtract/inversé = grand rectangle + chemins en evenOdd, intersect (et obscurcir) = clip séparé, **différence** = ou exclusif du groupe par la règle evenOdd (les packs d'effets image par image — Handy, Flame, BubbleFX — n'utilisent que ce mode) ; chemins de masque keyés ; **opacité de masque keyée** (animation image par image de FX Monster : un masque par dessin, opacité 0/100) → le chemin se réduit à un point là où elle est sous 50 % |
| track matte | clip par la géométrie du calque matte (ses peintures retirées) ; calque matte **à l'œil allumé** (AE 2023+, n'importe quel calque) : dessiné **et** copie de sa géométrie comme clip ; inversé = « tout » moins le matte en evenOdd, le grand rectangle posé **à la racine de l'artboard** avec une copie de la chaîne de transformations du matte au-dessus de sa géométrie (sous ses transformations, un matte réduit à l'échelle 0 emportait le rectangle : rig-test PL10) ; **précomp** comme matte : ses formes recopiées dans l'artboard, clés recalées sur la timeline (`inline_nested`) ; **contour** dans un matte : Rive ne découpe qu'avec l'intérieur des tracés → la bande peinte devient un tracé (Luau `ae_outline` après les trims/tirets : triangles de même sens, bouts ronds/carrés) — le balayage « horloge » (cercle au contour large comme son diamètre, trimé) d'rig-test passe de 50 % à 0,1–2 % d'écart ; **image** dans un matte : son rectangle découpe (Rive découpe avec des tracés ; l'alpha n'est pas tracé — retirée, elle vidait le matte : les glaces du zootrope de broadcast-test) ; mode de fusion **silhouette / pochoir** : le calque n'est pas dessiné, sa géométrie devient la découpe (inversée pour silhouette) de **tous les calques du dessous** ; **masque ouvert** : ignoré (AE ne découpe qu'avec des masques fermés — la guitare de broadcast-test portait une courbe ouverte de 8 points) ; Calcul composé « Copier » depuis un calque visible : calque non dessiné (il en répète les pixels) ; **Définir un cache** → découpe en espace de composition par la source avec ses transformations et ses parents, reconstruite à la racine (mesuré dans AE 26.5 : la rotation propre de la cible ne tourne pas le cache ; IoU 0,995–0,998 ; Glass 1,06 → 0,81 %) ; **précomp réduite** contenant un calque pochoir / silhouette → découpe aussi les calques placés sous elle dans la comp parente (sauf précomp à opacité 0 : variantes de format masquées par expression) ; un matte **nul** (calque nul : aucun pixel) ne laisse rien passer, ni un matte dont le propre matte est vide — AE 2023+ rend un calque matte avec son propre matte (Glass Animation 01 : N2 caché par un nul, N1 par N2, A par N1 → aucun dessiné, 1,25 → 1,06 %) ; luma ≈ alpha ; texte/image matte : non converti |
| effets | Transform (Geometry2) → nœuds exacts ; Fill, Tint, Invert, Exposure, Levels, Color Balance HLS, Luminosité/Contraste, **Teinte/Saturation** (global + « Redéfinir » : les packs FX se recolorent ainsi) → **appliqués aux couleurs** (et, posés sur un calque précomp, dans une variante de son artboard : tout ce qu'elle contient est recoloré) (exact sur du vectoriel, cuit dans les PNG) ; Gradient Ramp → vrai dégradé Rive (points en coordonnées de comp, clés si le calque bouge) ; le reste → commentaire `<!-- ae: effect … -->` (rml2ae le remet dans AE) + `effects_todo.json` ; calques d'effets (adjustment) → `effects_todo.json` (post-process WGSL à écrire) ; styles de calque (ombre portée…) : pas dans Rive, signalés au rapport |
| son | (`--no-audio` : rien) `AudioAsset` + `AudioEvent` déclenché au point d'entrée (WAV/MP3/FLAC, le reste converti en WAV par ffmpeg) ; calque son coupé à la partie qu'AE joue ; **le son d'une vidéo** sorti à part (WAV coupé à la plage utilisée) |
| modes de fusion | noms Rive (Add → `additive`) ; sans équivalent → le plus proche de la même famille (Lumière vive / linéaire / ponctuelle, Mélange maximal → `hardLight` ; Densité linéaire + → `multiply` ; Soustraction → `difference` ; Division → `colorDodge`) + rapport — dessinée « normal », la teinte cyan en Lumière vive d'rig-test cachait toute la transition ; les autres → normal |
| propriétés essentielles (Essential Graphics / propriétés principales) | chaque **instance** d'une précomp dont une propriété principale est surchargée (couleur, texte source, opacité, trim… ; statique ou keyée) → **variante** de l'artboard construite avec ces valeurs (`RIG_Clarisse 26 · instance 1`), une par jeu distinct ; les surcharges qui visent une précomp plus profonde descendent avec elle ; `essential_property_source` de py-aep relie la surcharge à sa propriété ; elle descend aussi dans les précomps qui la **lisent** par `comp("…")` (AE les rend dans le contexte de l'instance : broadcast-test, chaque glace du zootrope choisit sa vue ainsi). rig-test : le même rig blanc dans un plan, orange dans un autre (45–52 s : 52 % → 0,0 %) |
| solo | un calque en solo ne masque les autres images que s'il en a une : un calque son ou guide en solo (rig-test : « UniversalAudio-1 » dans chaque rig) laisse tout visible, comme AE |
| 3D, caméras | projection par la caméra active (défaut, un nœud, deux nœuds, parentée ; position par défaut = −zoom) : le calque garde sa transformation 2D aplatie F (celle qu'AE donne aux enfants 2D) et une homographie keyée par image la corrige. **Formes, solides, textes** : exact (Luau `ae_project` dans un `GroupEffect` ciblé par chaque peinture — les glyphes d'un `Text` prennent les effets de chemin) ; **images, vidéos, séquences** : exact (`ae_plane_image`, maillage 16×16, nom d'image keyé pour une séquence) ; **précomps** : rendues à plat dans un canvas puis plaquées (`ae_plane_artboard`, comme AE) ; **masques** projetés (sommets keyés) ; **cache 3D** : le clip suit la projection ; **« Orienter vers la caméra »** ; **ordre de profondeur** : trié, et s'il change en cours d'animation `DrawRules` keyés sur des emplacements vides ; `toComp`/`fromComp`/`toWorld` voient la 3D et la caméra. Approché (rapport) : épaisseur de contour non raccourcie, profondeur de champ, 3D par caractère, précomp réduite contenant de la 3D |
| Corner Pin (calque ordinaire) | même homographie, exacte (formes, textes, images) |
| calque d'effets Transformation | nœuds animés autour de tous les calques du dessous, identité hors de sa durée (les « Move » de broadcast-test) ; échelle uniforme = paramètre -0003 (hauteur), comme AE |
| billboard (orienter vers la caméra) | la rotation héritée des parents est **remplacée** (seules leurs position et échelle restent) : la glace de broadcast-test, parent à −78°, restait couchée |
| lumières | ignorées (rapport) |

**Banc AE (2026-09-29, AE 26.5, projet de test `~/.cache/ae2rml_tests/ae2rml_tests.aep`, 16 comps construites par
script puis rendues par `saveFrameToPng`)** : Zig Zag coins/lisse, Offset onglet et négatif arrondi, Pucker, Merge Paths
(peintures au-dessus ignorées : confirmé), Gradient Ramp sur une forme (respecte la forme : confirmé), Twist (3 sondes),
carte 3D + caméra deux nœuds, image / vidéo / solide / texte / précomp / masque / cache 3D, billboards, ordre de
profondeur qui s'inverse → **26 comparaisons AE | Rive entre 0,00 et 0,86 % de pixels différents** (anticrénelage).
`ae pull` testé dans AE : marquage par `tag_ae_project.jsx`, déplacement + rotation keyée avec ease retouchés dans AE,
repris dans scene.rml, rendu Rive = rendu AE (0,15 %).

**Vérifications (2026-09-29, sans AE).** Projection 3D contre les oracles de py-aep vérifiés contre AE 2026
(`source_point_to_comp` pour la caméra par défaut, `camera_ray` pour les caméras un/deux nœuds tournées) : écart
≤ 1e-12 px ; pli 3D de `TR_Fold Right` rendu dans Rive vs `toComp` des repères par la caméra : ≤ 1,1 px sur les bords.
Twist et Pucker comparés aux GIF de rendu AE posés à côté des .aep du stock ; Merge Paths : Simple_Elements 2/6,
Complicated_Elements 4, Blowups 2 retrouvent leurs contours creux ; TR_Flares 01 : flare vidéo + 3 séquences PNG en
`Solo`, exposition animée cuite par image.

**Bidirectionnel.** `build/ae2rml/tag_ae_project.jsx`, lancé une fois dans le projet AE d'origine (Fichier > Scripts),
pose `rive:<id>` sur chaque calque et `rive:<artboard>|<animation>` sur chaque comp (seulement là où le commentaire est
vide ou déjà un tag). Ensuite `ae pull mon_projet_rive` relit les retouches faites dans AE (transformations, clés, eases)
vers `scene.rml`, et une réimportation garde les mêmes ids (`idmap.json`). Testé dans AE (banc ci-dessus).

**Banc sur projets réels (2026-09-29, AE 26.5, lecture seule).** `real_test.py` (scratchpad) : conversion, rendu AE par
`aerender` (ne sauve rien ; `-i` limité à 100 → une passe par image au-delà) et captures Rive aux mêmes images, IoU du
dessiné. Les copies ouvertes dans l'AE graphique vivent dans `~/.cache/ae2rml_tests/real/` et se ferment sans
enregistrer. Avant → après les corrections de la soirée : Arcs 0,70–0,99 → **0,97–0,99** ; Transitions 6 0,12–0,92 →
**0,97–1,0** ; Complicated_Elements 4 0,58 → **0,92–1,0** ; Shape_01 (FX Monster) 0,0 → **0,98–0,999** ; Cartoon_01
(FX Monster) plantage → **0,96–0,995** ; Web Elements 01 : textes et arrondis → **1,0** sur 3 images (reste le
placeholder « 4-Color Gradient ») ; Glass Animation 01 (Motion Bro) : géométrie à 4 px près (restent flou/verre raster et
la police Archivo absente) ; LineCallOut 0,70 → **0,985** (le trait suit ses nuls ; l'échelle en JSXBIN cuite par `bake_expressions.jsx` dans AE, validé ; police manquante remplacée par Helvetica comme le fait AE) ;
CARTIER_LOGO, Arrow Icon, Blowups 6, Simple_Elements 6, montage_son ≈ 0,97–1,0. Projets de the author ouverts dans AE :
Mh_LST_Resp_Drinking_01 (50 s, balayage d'une image sur 15) pire écart 3,8 % → **0,79 %**, moyenne 0,26 % ;
rig-test (compo PCRH, 118 s, 64 artboards, balayage d'une image par seconde) : médiane 0,5 % → **0,3 %**,
moyenne 15,5 % → **7,2 %** — les pics restants sont des médias introuvables (AE y dessine des mires : 0–1 s, 89–96 s,
112–118 s) et le déplacement glitché de la transition « rembobinage » (Displacement Map, à écrire en WGSL) ; contrôle
visuel des 14 projets réels déjà rendus (`vis_check.py`) : égaux ou meilleurs, sauf deux images à +0,1 / +0,3 % ; Mh_Safety_film03
(4 min 51, 70 vidéos) converti et vérifié, .riv de 425 Mo en médias allégés. `aerender` quitte en silence au bout de
9 s sur promo-test et B3_S1_BNP_01 (AE ouvert ou non) : à comparer par l'AE graphique.

**Pièges py-aep 0.17 mesurés** (contournés dans le code) : clés à des temps fractionnaires (projets étirés) — replacées
exactement sur les images ; masques d'un calque de **forme ou de texte** stockés en pixels mais multipliés par la taille
du calque comme ceux d'un métrage (x = −1 177 920 au lieu de −613,5) ; paramètre d'effet **non stocké** dans l'instance (curseurs, pseudo-effets comme TextEvo) : py-aep prend le champ
« valeur » du `pard` de la définition (75 dans Glass Animation 01, opacité 0 dans TextEvo → chiffres invisibles) alors
qu'AE prend le **défaut** : flottant à l'octet 48+72 du `pard` d'un curseur, uint16 à 48+14 d'une liste, **octet 48+12 d'une case à cocher** (`param_defaults` ; lu 0 d'office, il envoyait le « on / off » et l'« absolute » de Randomatic — cochés par défaut — dans la branche éteinte : vérifié dans AE 26.5, qui lit on/off = 1, strength = 100 là où py-aep lit 108) ;
plusieurs styles de caractères dans un texte (Book puis Medium) → un `TextStylePaint` + `TextValueRun` par plage ;
animateur « par lignes » d'un paragraphe en boîte → lignes **composées** par AE (`composed_line_count`), pas les retours ;
texte centré/à droite : AE centre sans les espaces de fin de ligne (Rive les garde : demi-espace de décalage) ;
`apply_fill` du texte à None (7 textes sur 8 invisibles) ; la justification du texte s'imprime en nombre (`str()` → « 7415 », pas CENTER_JUSTIFY : tous les textes centrés sortaient alignés à gauche — lire `.name`) ; noms des paramètres de pseudo-effets (« Color 1 » au lieu de
« Background ») ; l'opacité d'un calque nul est rendue à 0 % alors qu'AE stocke
100 % (vérifié contre le dump ExtendScript de `ae pull` et le chunk brut) ; py-aep synthétise des propriétés absentes du
fichier avec leurs valeurs par défaut (les 6 tirets d'un contour, les 473 propriétés d'un animateur de texte) → filtrées
par `_stored()` ; les noms localisés arrivent en « surrogate escapes » (`Point d\udcb4ancrage`) → `clean()` ; un segment
spatial droit « sortie linéaire / entrée en ease » est interpolé en ligne droite par py-aep (l'ease d'entrée ignorée),
alors que py-aep note lui-même qu'AE honore cette ease en 1D : ae2rml la garde — c'est la seule source d'écart de
l'oracle `python -m rml2ae.ae2rml.check_keys *.aep` (clés Rive évaluées vs interpolation py-aep, image par image :
1 597 pistes du stock, erreur médiane 0). Les 6 .aep illisibles du stock sont des projets **AE CS6 (11.0.1)** : ouverts
puis enregistrés en copie par AE 26 (script, `~/.cache/ae2rml_tests/resaved/`), ils se convertissent tous (`--verify` OK).
AE affiche à l'ouverture deux dialogues que `beginSuppressDialogs()` ne supprime pas (conversion de version, médias
introuvables) : prévenir the author, il faut cliquer OK.

Mesuré sur le stock de the author (`Travail/PRO/00_SAVE_AE`) : les projets de 1 à 5 000 calques se lisent en 0,02 à 4 s ;
DESIGN/promo-test → 65 artboards, `--verify` sans erreur, rendu fidèle (glissements de texte, masques, mattes,
dégradés, corrections couleur) ; aller-retour `features` (RML → AE → RML) : dégradés, tirets, précomps, textes, mattes OK.

## Effets AE natifs en WGSL — `ae2rml/fxlib/` (2026-10-03/04)

Un effet raster d'AE sans équivalent en objets Rive (sur métrage, précomp ou calque d'effets) devient un **nœud WGSL**
dans Rive, et ce nœud **redevient l'effet AE natif** au retour (rml2ae). Aller-retour AE → Rive → AE mesuré au pixel
(0,000 % sur Tint → Invert, ≤ 0,007 % sur Remplir + calque d'effets).

- **Bibliothèque :** chaque effet = `ae_<slug>.wgsl` + `<slug>.json` (manifeste). Les uniforms sont les paramètres AE en
  **unités AE brutes**, adressés par leur **position** (`property(i)`). Attention : la position n'est pas toujours le
  numéro du matchName (Couleur de Remplir = `property(3)` = `ADBE Fill-0002`), d'où `_ae_params.json`, mesuré dans
  AE 2026. Le contrat complet est dans la docstring de `fxlib.py`.
- **37 effets vérifiés** (04/10) contre des rendus AE 2026, chacun sur au moins un réglage **jamais montré** à
  l'implémentation (`python -m rml2ae.ae2rml.fxlib check <slug> --rive --holdout`, `.venv`) :
  - **exacts (26)**, ≤ 1–2 niveaux : Remplir, Teinte, Exposition, Inverser, Niveaux, Estampage, Juxtaposition (Motion
    Tile), Ombre portée, Dégradé 4 couleurs, Gamme des dégradés, Flou gaussien, Flou gaussien et Flou accéléré
    (hérités — même noyau, mesuré), Flou encadré, Flou directionnel, Quatre coins, Postérisation, Décaler les couches,
    Damier, Volet linéaire, Trois tons, Teinte/Saturation, Luminosité/Contraste, Équilibre des couleurs (TLS),
    Minimax, CC Scale Wipe ;
  - **proches (8)** : Dilaté-érodé simple, Dispersion (Displacement Map), Noir et blanc (option teinte ±1), Incrustation
    de luminance (contour progressif, max 7), Lueur (Glow, max 7–11 en quelques points), Loupe, CC Radial Fast Blur,
    Correction optique (bords adoucis par AE à grand angle) ;
  - **approchés (3)** : Mosaïque (tuiles non entières), CC Radial Blur (le type « Centered Zoom », déduit, est faux :
    23 % des pixels), Turbulent Displace (le bruit d'AE est propriétaire : même échelle, motif différent).
  - **Non appliqués automatiquement** par ae2rml (`"auto": false` dans le manifeste, l'effet reste un commentaire à
    faire) : Turbulent Displace (Shape_01 : 0,05 → 0,8 % de pixels faux avec lui) et CC Radial Blur (promo-test à
    12 s : 0,07 → 7,3 %). Mesuré sur les projets réels, l'effet absent est plus proche d'AE que l'effet approché.
  - Deux effets ont été corrigés **sur** leur réglage caché (Postérisation /255 au lieu de /256, saturation positive de
    l'Équilibre TLS) : pour eux le contrôle indépendant est consommé, c'est noté dans `verified.note`.
  - Champ réservé `layerRect` (vec4, après `passIndex`) : le rectangle du calque dans le canevas, agrandi par `fxPad`
    quand un effet de la pile déborde ; Volet linéaire et CC Scale Wipe s'en servent.
  - Pièges du parseur WGSL de Rive (acceptés par l'oracle hors ligne) : `]]` (écrire `let i = los[k]; A[r - i]`) et
    `a < b.x && c >= 0` lu comme un type générique (parenthéser).
  Statut et mesures sont dans chaque manifeste (`status`, `verified`).
- **ae2rml :** un effet sans voie native qui figure dans la bibliothèque devient UN nœud Luau généré
  (`fx_<slug>[__<slug>…].luau`) pour **toute la pile d'effets** du calque, dans l'ordre d'AE.
  - Des nœuds imbriqués ne rendent rien dans Rive (mesuré) : toutes les passes de la pile tournent donc dans un seul
    nœud, en alternant entre 3 canevas GPU.
  - Le contenu va dans un sous-artboard (`FxArtboard` : ses clés quittent l'animation de la comp).
  - Pour un **calque d'effets**, tous les calques du dessous vont dans le sous-artboard ; son opacité et sa fenêtre
    in/out deviennent `fxMix` (passe `_ae_fx_mix.wgsl`, lerp exact).
  - Un effet couleur sur des **pixels** (métrage, précomp contenant du métrage) passe aussi par le WGSL : recolorer des
    peintures vectorielles laissait l'image intacte.
- **Calques d'effets (04/10) :**
  - plusieurs calques d'effets empilés donnent UN nœud, avec un groupe par calque (`g<j>Mix` / `g<j>Blend`) ;
  - le **mode de fusion** d'un calque d'effets est appliqué selon la règle mesurée dans AE : composition W3C de
    l'effet sur l'original, re-découpée par l'alpha de l'original, puis interpolée par l'opacité (Incrustation à
    50 % : 0,09 %) ;
  - les calques d'effets d'une **précomp réduite** s'appliquent aux calques sous elle dans la comp parente
    (promo-test à 2 s : 3,9 % → 0,6 % d'écart).
- **Limite Rive :** un nœud d'effet dessiné dans le canevas d'un autre script ne rend rien (une toile 2D ne montre
  pas l'image d'une toile GPU, même amorcée hors de son cadre ; mesuré). ae2rml neutralise donc le nœud intérieur
  (`fxMix` 0 : contenu tel quel) et le signale ; l'effet extérieur fonctionne.
- **py-aep** lit parfois 100× trop grands l'ancre et la position de l'effet Transformation (promo-test :
  96000 au lieu de 960, même après réenregistrement). Les valeurs hors de 10× la taille du calque sont divisées
  par 100 et le rapport le signale.
- **rml2ae :** nœud `fx_…` → précomp de la source + les effets AE natifs dans l'ordre, paramètres statiques ou
  keyés ; `fxMix` → Effect Opacity (Compositing Options). Le mix d'un groupe va sur l'Effect Opacity de chacun de
  ses effets ; le mode de fusion est signalé (il faudrait un calque d'effets dans AE).
- **Pièges :**
  - `pass` est un mot réservé WGSL (le champ s'appelle `passIndex`) ;
  - uniformité naga : utiliser `textureSampleLevel` dans les boucles et les branches ;
  - le PNG de `saveFrameToPng` est prémultiplié ;
  - la capture `rive --advance=T` montre T − 1/60 s : les outils de comparaison capturent Rive à T + 1/60.
- **Reste :**
  - calques d'effets dans une précomp **réduite**, qui s'appliquent aux calques sous la précomp dans la comp parente
    (promo-test) ;
  - mode de fusion d'un calque d'effets (Incrustation…) dans la passe de mélange ;
  - 19 effets du recensement encore à écrire, dont Lueur raster, Teinte/Saturation, Luminosité et contraste, CC Radial
    Fast Blur, Turbulences, Incrustation luminance, Volet linéaire et Trichrome.

## Plan v2 (chantiers séparés)

1. **`figma2rml`** — Figma Design → projet Rive CLI (un artboard par frame, `scene.rml` + `assets/` + `board.md` résumé
   pour l'agent). Base : API REST Figma (ou un exporteur open source type figma-export) → SVG + JSON des nœuds →
   `figma2rml/svg2rml.py` + une couche neuve pour ce que le SVG perd (noms, textes comme
   `Text` + polices, images en `ImageAsset`, blend modes, auto-layout → `LayoutComponent`, composants → artboards).
   Objectif : zéro token pour la géométrie, l'agent ne lit que le résumé. Figma Design seulement (pas FigJam/Slides).
2. **Bibliothèque d'effets par commentaires RML** — `<!-- ae: DropShadow … -->` sur un élément : rml2ae pose l'effet AE
   natif, le viewer Rive l'ignore ou joue un WGSL équivalent fourni par le package. C'est ce qui permet à la route
   Figma → RML → AE de garder les ombres/flous/dégradés angulaires qu'un plugin Figma → AE direct conserve et que Rive
   n'a pas ; `figma2rml` les écrit.
3. `ae pull` étendu (couleurs, chemins, texte), converters de binding, fondu de transition en mélange de valeurs,
   plugin GPU natif + signature Developer ID.

## Open source

- `schema.json` est regénéré depuis les en-têtes MIT de rive-runtime (`tools/make_schema.py`) : rien ne provient du
  binaire fermé du CLI. Le SDK Adobe (non redistribuable) et les dépendances du plugin vivent hors du dépôt
  (`~/.cache/ae-plugin-deps`). Projet indépendant, non affilié à Rive Inc. ni à Adobe.
- Licence du dépôt : à choisir (MIT/Apache-2.0 cohérent avec rive-runtime et wgpu).
- Installation : `INSTALL.md`.
