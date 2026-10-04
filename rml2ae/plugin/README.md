# Rive Shader — plugin After Effects

Effet SmartFX (« Rive » › « Rive Shader », match name `RIVE RiveShader`) qui exécute un `.wgsl` Rive sur un calque :
le calque = texture 0, `struct Params` → paramètres d'effet, textures supplémentaires → paramètres Layer. Rendu par
wgpu-native (Metal) embarqué en statique : upload → passe plein écran → readback, 8/16/32 bpc.

## Build / installation (dev)

```bash
./build.sh            # CMake+Ninja → PiPL (Rez) → signature ad hoc → ~/AE-Dev-Plugins/RiveShader.plugin
./build.sh clean      # rebuild complet
./build.sh dist       # + zip dans ~/.cache/ae-plugin-deps/dist/ (SIGN_IDENTITY="Developer ID Application: …" pour signer)
./build.sh install    # + copie sur Plug-Ins/RiveShader.plugin (AE fermé) — c'est la copie qu'AE 26.5 charge
```
Dépendances (hors dépôt) : `~/.cache/ae-plugin-deps/AfterEffectsSDK` (SDK 26.5, console Adobe) et
`~/.cache/ae-plugin-deps/wgpu-native` (release v29.0.1.1 macOS arm64). Dossier de test lié une fois pour toutes :
`sudo ln -s ~/AE-Dev-Plugins "/Applications/Adobe After Effects 2026/Plug-ins/RiveDev"` — AE se relance pour recharger.
**Constat 04/10 (AE 26.5) :** AE charge la copie installée `Plug-Ins/RiveShader.plugin` (build du 17/09) et le
`Plugin Loading.log` ne mentionne plus du tout le dossier lié `RiveDev` : un nouveau build n'est donc pris en compte
qu'une fois copié à la place de la copie installée : `./build.sh install` (AE fermé). Le build se fait avec le SDK de Xcode (le SDK 27 des Command Line Tools casse l'édition de liens).

## Convention WGSL (celle d'un `post.wgsl` de projet)

- `@group(0) @binding(0)` texture source (le calque, prémultipliée), `(1)` sampler linéaire/clamp, `(2) var<uniform> P: Params`,
  `(3+)` textures supplémentaires → « Texture 1..3 » (calques ; vide = 1×1 transparent). Un passe `vs_main` triangle couvrant + `fs_main`.
- `struct Params` : `f32 / i32 / u32 / vec2 / vec3 / vec4`, layout uniform WGSL. Champs réservés remplis par l'hôte : `size`
  (taille du calque), `tick` (« Step fps », 0 = index d'image), `fxTick` (« FX fps », 0 = tick), `seed` (« Seed »), `pad*`.
- Les autres champs, dans l'ordre : `vec2` → « Pt1..4 » (pixels du calque) ; `vec3/vec4` dont le commentaire contient
  « color » → « C1..4 » ; le reste → sliders « P1..16 », un scalaire par slider. Le commentaire `// 0..1 default 0.06`
  donne la plage du slider et la valeur appliquée quand on change de shader.
- Le shader est choisi par le slider « Shader » (id entier, scriptable) résolu via `~/Library/Application Support/RiveShader/shaders.tsv`
  (`id<TAB>chemin`) ; le bouton « Load .wgsl… » enregistre un fichier et règle le slider. Recompilé quand le fichier change (mtime).
- Erreur (id inconnu, fichier illisible, WGSL invalide, GPU absent) : l'entrée est recopiée telle quelle, le nom du
  paramètre « Shader » l'indique, le détail est dans `~/Library/Application Support/RiveShader/riveshader.log`. Jamais de boîte de dialogue.

Index des paramètres (ExtendScript `fx.property(i)`) : 1 Shader · 2 bouton · 3 Step fps · 4 FX fps · 5 Seed · 6–21 P1..P16 ·
22–25 C1..C4 · 26–29 Pt1..Pt4 · 30–32 Texture 1..3.

## Repère du shader dans une pile d'effets (corrigé le 04/10)

Le shader travaille toujours sur le **rectangle du calque** (comme le canvas Rive = l'artboard) : `uv` 0..1, `size` et les
points « Pt » sont ceux du calque, quoi qu'aient fait les effets au-dessus. Avant, il travaillait sur le tampon reçu :
un Drop Shadow / Glow / flou au-dessus l'agrandit (le disque de test glissait de 15 / 42 / 33 px et le dégradé
s'étirait), un masque le rétrécit (le disque était rogné). Ce qui déborde du calque (le halo d'un effet au-dessus)
passe tel quel, non shadé, pour que l'effet suivant le retrouve. Les effets en dessous n'étaient pas touchés.
Test : `test/stacktest.py` (disque de `params_test.wgsl` seul, sous Drop Shadow / flou / Glow, au-dessus de Drop
Shadow / flou, avec un masque ; position du disque + comparaison à l'oracle dans le calque + passage à l'extérieur).
Mesuré dans AE 26.5 le 04/10 : 8 cas sur 8, disque à sa place, 0/255 contre l'oracle (avant : disque décalé de
15 / 33 / 42 px, rogné sous le masque) ; copytest, shadertest et maptest toujours à 0/255.

## Tests (`test/`)

| script | quoi | résultat 2026-09-17 (AE 26.2.1, M4 Max) |
|---|---|---|
| `oracle.py` | `rs_apply` (module GPU seul) vs `rml2ae/wgsl_apply.py` | 8 bits identique au bit, float ≤ 1/255, 3,25 ms/image 1080p, RSS plate sur 6000 rendus |
| `copytest.py` | copy-through 8/16/32 bpc, GrowBounds, masque, décalage | 0/255 |
| `shadertest.py` | `post.wgsl` dans AE vs oracle, tick, id inconnu, shader cassé | 0/255 (8), ≤ 1 (16), passthrough OK |
| `maptest.py` | texture par calque, couleur, point, sliders | 0/255 |
| aerender × 5000 | mémoire | malloc + Metal plats (vmmap), la croissance est le cache d'images d'AE |

Les harnais refusent de tourner si le projet AE ouvert n'est pas vide (ou ne contient que leurs propres éléments) et
font ≤ 10 `saveFrameToPng` (au-delà, l'UI d'AE gèle). Les rendus longs passent par `aerender` sur un `.aep` sauvé dans `~/.cache`.

## À faire — effet « Rive Mesh » (demandé par the author le 04/10)

Une image à maillage Rive (sommets, UV, triangles, peau sur os) sort aujourd'hui dans AE en 8 bandes Corner Pin
(rml2ae `emit_mesh`) : approché, non retouchable. Les outils natifs ne collent pas (Mesh Warp = grille, Marionnette =
triangulation automatique). Plan :
- **v1 exacte** : un effet du même plugin (ou un 2ᵉ match name) qui lit un fichier compagnon — maillage + positions des
  sommets par image, cuites depuis Rive — et dessine les triangles texturés par le calque (tampon de sommets au lieu du
  triangle plein écran). rml2ae l'émet à la place des bandes ; mesure contre la capture Rive.
- **v2 retouchable** : peau calculée en direct depuis des nulls AE (un par os) choisis dans l'effet.

## Hors périmètre v1

Multi-passes orchestrées par Luau (une instance d'effet par passe), compute/storage/VBO, `@group` > 0, état persistant entre
images, chemin GPU natif d'AE (`PF_Cmd_SMART_RENDER_GPU`, v2), `PF_OutFlag2_SUPPORTS_THREADED_RENDERING` (le module GPU est
sérialisé par un mutex ; à activer seulement après une revue de réentrance), signature Developer ID + notarisation (pas de certificat).
