# Third parties, trademarks and sources

**Independent project. Not affiliated with or endorsed by Rive Inc. or Adobe Inc.** "Rive" and "After Effects" are
trademarks of their owners and are only used to name the software these tools work with.

Nothing below is redistributed in this repository unless stated.

| Component | Use | License / terms |
|---|---|---|
| **Rive CLI** and viewer | authoring, verifying and rendering Rive CLI projects | installed from Rive by `install.sh` (Homebrew tap `rive-app/tap` or `releases.rive.app`, sha256 checked); never copied here |
| **Rive runtime** headers | `rml2ae/schema.json` is generated from the public headers of [rive-runtime](https://github.com/rive-app/rive-runtime) by `rml2ae/tools/make_schema.py`; the `.riv` reader follows the same format | MIT (© Rive) — `rml2ae/LICENSE.rive-runtime.txt` |
| **py-aep** ([forticheprod/py-aep](https://github.com/forticheprod/py-aep)) | reads `.aep` files for ae2rml, without After Effects | MIT (© Fortiche Prod) — installed with pip |
| **Adobe After Effects SDK** | builds the Rive Shader plugin | Adobe SDK terms (free download, compiled plugins may be distributed); not included |
| **wgpu-native** | GPU backend of the plugin (static) | MIT / Apache-2.0 — downloaded by `build.sh` |
| **Montserrat** | font of the synthetic test case | SIL Open Font License 1.1 — `rml2ae/tests/cases/features/OFL-Montserrat.txt` |

ae2rml reads a few bytes py-aep does not expose (default parameter values in `pard` blocks, pseudo-effect
definitions, PSD layers): file reading for interoperability; no Adobe code is decompiled or copied.
