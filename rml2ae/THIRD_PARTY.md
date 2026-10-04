# Tiers, marques et sources — rml2ae / figma2rml / motion

**Projet indépendant. Non affilié à, ni approuvé par, Rive Inc., Adobe Inc. ou Figma Inc.** « Rive », « After Effects »,
« Figma » sont des marques de leurs propriétaires ; elles ne sont employées ici que pour désigner les logiciels avec
lesquels ces outils travaillent.

## Ce que ce paquet contient d'origine tierce
- **Schéma des types Rive** (`rml2ae/schema.json`) : généré par `rml2ae/tools/make_schema.py` à partir des en-têtes
  publics de [rive-runtime](https://github.com/rive-app/rive-runtime), licence MIT (© 2020 Rive) — texte dans
  `LICENSE.rive-runtime.txt`. Le lecteur de fichiers `.riv` (`rml2ae/tools/riv_curves.py`) suit le format documenté par
  ce même code MIT. Aucun code de Rive n'est copié ni redistribué.
- **Rive CLI et son viewer** : non redistribués. L'utilisateur les installe depuis Rive
  (`brew install --cask rive-app/tap/rive-cli`, ou téléchargement direct depuis `releases.rive.app` proposé par
  l'installeur), gratuitement, sans compte ; `rive login` ne sert qu'à `push`.
- **Adobe After Effects SDK** : non redistribué. Le plugin `RiveShader.plugin` est compilé avec ce SDK selon les termes
  de sa licence (distribution des plugins compilés autorisée) ; les `matchNames` d'effets sont de l'API documentée.
- **Figma** : API REST officielle, en lecture seule, avec le jeton personnel de l'utilisateur ; rien n'est écrit dans
  Figma. Les boards convertis appartiennent à leurs auteurs et ne font pas partie du paquet.
- **Montserrat-Bold.ttf** (`figma2rml/fonts/`, projet de test) : SIL Open Font License 1.1 — `figma2rml/fonts/OFL.txt`.
- **Presets d'easing KeyframeEase** de Davide Boscolo (https://davideboscolo.com/preset-libraries-to-download/) : NON
  inclus. `motion/kease.py` convertit le dossier que vous téléchargez vous-même chez l'auteur
  (`python3 motion/kease.py "…/Easing library"` → `motion/easing_presets.json`, ignoré par git et par le zip).
- **Base motion** (`motion/MOTION_PRINCIPLES.md`) : chiffres mesurés sur des fichiers publics de la communauté Rive
  (trois fichiers d'icônes, de scène narrative et de jeu) et deux Lottie ; les fichiers eux-mêmes
  ne sont pas redistribués. Courbes de Penner et de Material Design : formules publiques.
- **svg2rml** (`figma2rml/svg2rml.py`) : code maison.
- **py-aep** ([forticheprod/py-aep](https://github.com/forticheprod/py-aep), licence MIT, © Aurore Delaunay / Fortiche
  Prod) : dépendance d'ae2rml (`pip install py-aep`), non redistribuée. Elle lit le format binaire `.aep` (RIFX) sans
  After Effects. ae2rml la corrige au chargement (`_patch_py_aep`) et lit lui-même quelques octets qu'elle n'expose pas
  (valeurs par défaut des paramètres dans les blocs `pard`, définitions des pseudo-effets, calques de PSD) : lecture de
  fichiers pour l'interopérabilité, aucun code d'Adobe n'est décompilé ni copié.
