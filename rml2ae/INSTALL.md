# Installer rml2ae + le plugin Rive Shader + le panneau AE (macOS, After Effects 2026)

## 0. Le plus simple : l'installeur
```bash
bash rml2ae/install.sh
```
(depuis ce dossier, ou depuis le zip produit par `rml2ae/make_dist.sh` décompressé n'importe où). Il crée un venv
Python avec les dépendances, vérifie le Rive CLI (propose `brew install --cask rive-app/tap/rive-cli`) et ffmpeg, met la
commande `ae` sur le PATH, copie le panneau dans After Effects (mot de passe admin, chemins réécrits pour l'endroit où
tu l'as décompressé), copie le plugin Rive Shader s'il est construit, puis lance `ae doctor`. Il ne touche à rien sans
te le dire. Reste à cocher dans AE : Préférences › Scripts et expressions › « Autoriser les scripts à écrire des
fichiers et accéder au réseau », puis relancer AE → Fenêtre › Rive.jsx. Le zip contient `docs/` (tous les guides).

Les sections suivantes détaillent ce que l'installeur fait, pour le faire à la main.

Rien à « compiler » pour le convertisseur (Python) ; le plugin est déjà construit par `plugin/build.sh` et se copie.

## 1. Prérequis
- macOS Apple Silicon, After Effects 2026 installé (`/Applications/Adobe After Effects 2026/`, avec `aerender`).
- Rive CLI : `brew install --cask rive-app/tap/rive-cli`, ou l'installeur propose de le télécharger directement depuis
  `releases.rive.app` (manifest public + sha256, le même que le cask) dans `~/.rive/bin` — pas besoin de Homebrew → `rive --version`.
- Python 3.9+ avec PIL, numpy, fontTools (et `wgpu` pour le prototype `wgsl_apply`) :
  ```bash
  python3 -m venv .venv && .venv/bin/pip install pillow numpy fonttools wgpu
  ```
  (`bin/ae` utilise `.venv/bin/python` s'il existe, sinon `python3`.)
- ffmpeg (`brew install ffmpeg`) pour `ae render --out x.mp4`.
- Les polices des projets installées dans macOS (AE les cherche par nom PostScript ; `ae doctor <projet>` liste les fichiers).

## 2. Le convertisseur
```bash
cd ae-rml-rive-cli
bin/ae doctor mon_projet                      # tout doit être [ok]
bin/ae build mon_projet                       # AE ouvert → nouveau projet + build/rml2ae/mon_projet.aep
bin/ae diff mon_projet --times 5 9.5 23.2     # planches AE / Rive
```
Ou directement : `.venv/bin/python -m rml2ae <projet> [--run] [--shots …] [--no-replay] [--all-animations] [--duration S]`.

## 3. Le bouton dans After Effects (panneau ancrable)
```bash
sudo cp rml2ae/panel/Rive.jsx "/Applications/Adobe After Effects 2026/Scripts/ScriptUI Panels/"
```
Relancer AE → **Fenêtre › Rive.jsx** : choisir le dossier projet, cliquer « Update from RML → this project » ;
« Pull this project's edits → RML » renvoie tes retouches AE (transformations, clés, masqué) dans le RML.
Le chemin de Python/racine du dépôt est écrit dans le fichier (variable `ROOT` en tête) : l'adapter si le dossier bouge.

## 4. Le plugin Rive Shader
Construit par `rml2ae/plugin/build.sh` (dépendances hors dépôt : SDK Adobe 26.5 et wgpu-native dans `~/.cache/ae-plugin-deps`).
Le résultat est `~/AE-Dev-Plugins/RiveShader.plugin` ; AE le voit par un lien dans son dossier Plug-ins (une fois, admin) :
```bash
sudo ln -s ~/AE-Dev-Plugins "/Applications/Adobe After Effects 2026/Plug-ins/RiveDev"
```
Sur une autre machine : copier `RiveShader.plugin` dans `/Applications/Adobe After Effects 2026/Plug-ins/` (signature ad hoc :
macOS peut demander une autorisation dans Réglages › Confidentialité ; une signature Developer ID + notarisation lève ça —
`SIGN_IDENTITY=… ./build.sh dist`). Vérification : `bin/ae doctor` → « Rive Shader plugin [ok] », et dans AE l'effet
« Rive › Rive Shader ». Sans plugin, `rml2ae` retombe sur des effets AE natifs (Noise / Turbulent Displace / vignette / wiggle).

## 5. Fichiers à côté d'un projet (optionnels)
- `ae_passes.json` : passes texte/photo d'un post-process (surcharges de ViewModel par passe).
- `ae_audio.json` : `[{"comp": "Main", "file": "build/soundtrack.wav", "at": 0}]` → calques son.

## 6. Ce qui reste manuel
Sauver le projet AE avant `ae build --replace`/`watch` (ils construisent dans le projet ouvert) ; ne pas laisser de boîte
de dialogue ouverte dans AE pendant un build (les scripts seraient bloqués).
