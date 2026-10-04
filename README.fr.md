# AE RML via CLI Rive

Aller-retour entre **Adobe After Effects** et **Rive**, en ligne de commande, via le format texte du
[Rive CLI](https://rive.app) (`scene.rml`) :

- **ae2rml** — un projet After Effects (`.aep`) devient un projet Rive CLI sans lancer After Effects (lecture du `.aep`
  par [py-aep](https://github.com/forticheprod/py-aep) de Fortiche). After Effects reste nécessaire une fois pour les
  expressions aléatoires (`random()`, `wiggle()`, `noise()`) ou compilées (`bake_expressions.jsx`), les projets CS6 à
  réenregistrer, et le marquage du projet avant `ae pull` (`tag_ae_project.jsx`).
- **rml2ae** — un projet Rive CLI devient un projet After Effects (une comp par artboard, vraies clés, calques de
  forme, textes, mattes), reconstruit de façon incrémentale ; les retouches faites dans AE reviennent par `ae pull`.
  Un fichier du **Rive Editor** (`.rev`) passe dans After Effects de la même façon, une fois transformé en projet par
  le Rive CLI (`rive create <dossier> --from-rev=fichier.rev`, ou `--from-remote-file` pour un fichier de ton compte
  Rive) ; `rive push` envoie le projet vers le Rive Editor (le fichier lié dans ton compte) et `rive open <fileId>`
  l'y ouvre.
- **Rive Shader** — un plugin d'effet After Effects qui exécute tel quel un shader post-process Rive (`.wgsl`).
- **fxlib** — des effets natifs d'After Effects réécrits en WGSL et mesurés contre AE, pour qu'ils passent dans les deux sens.
- **kit de relecture** — relecture façon Frame.io dans le **Rive Viewer** (`rive <projet>`, le viewer du Rive CLI) :
  timeline, scrub, notes dessinées et tapées.

Le schéma, l'installation et l'usage sont dans `README.md` (identiques) :

```bash
./install.sh                      # macOS : tout
./install.sh rml2ae ae2rml        # ou seulement certaines parties : rml2ae  ae2rml  review-kit  plugin  skills
```

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1                  # Windows : tout
powershell -ExecutionPolicy Bypass -File install.ps1 rml2ae ae2rml    # ou seulement certaines parties (mêmes noms)
```

Le plugin Rive Shader se compile avec `build.sh` sur macOS et `build.ps1` sur Windows (voir `docs/install.md`).

Le **Rive CLI n'est pas dans ce dépôt** : l'installeur le récupère chez Rive (tap Homebrew `rive-app/tap` ou
`releases.rive.app`, sha256 vérifié). Licence MIT.
