#!/bin/bash
# AE RML via CLI Rive — installer (macOS).
#
#   ./install.sh                     everything below
#   ./install.sh rml2ae ae2rml       only what you name:  rml2ae  ae2rml  review-kit  plugin  skills
#
# The Rive CLI is never shipped here: it is installed from Rive (Homebrew tap rive-app/tap or releases.rive.app,
# sha256 checked) by rml2ae/install.sh.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ok()   { printf '  \033[32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m[--]\033[0m %s\n' "$*"; }
want() { [ $# -eq 0 ] && return 0; for a in "${PARTS[@]}"; do [ "$a" = "$1" ] && return 0; done; return 1; }
PARTS=("$@"); [ ${#PARTS[@]} -eq 0 ] && PARTS=(rml2ae ae2rml review-kit plugin skills)

if want rml2ae || want ae2rml || want review-kit; then
  echo "== core: Python env, Rive CLI, the 'ae' command, the After Effects panel (rml2ae/install.sh)"
  bash "$HERE/rml2ae/install.sh"
fi
if want ae2rml; then
  echo; echo "== ae2rml (.aep -> Rive CLI project): reads .aep files with py-aep, no After Effects needed"
  "$HERE/.venv/bin/pip" install -q py-aep && ok "py-aep (https://github.com/forticheprod/py-aep, MIT)"
  "$HERE/.venv/bin/pip" install -q wgpu >/dev/null 2>&1 && ok "wgpu (optional: offline checks of the WGSL effect library)" \
    || warn "wgpu not installed (only needed for 'python -m rml2ae.ae2rml.fxlib check')"
  echo "     try:  .venv/bin/python -m rml2ae.ae2rml examples/demo.aep out/demo --verify"
fi
if want review-kit; then
  echo; echo "== review kit (Frame.io-like notes inside the Rive CLI viewer)"
  ok "nothing to install: python3 review-kit/review_install.py <your Rive CLI project>   (see review-kit/README.md)"
fi
if want plugin; then
  echo; echo "== Rive Shader plugin for After Effects (runs a Rive .wgsl as an effect) — built from source"
  if [ -d "${AE_SDK:-$HOME/.cache/ae-plugin-deps/AfterEffectsSDK}" ]; then
    (cd "$HERE/rml2ae/plugin" && bash build.sh install) && ok "plugin built and installed (quit After Effects first)"
  else
    warn "needs the Adobe After Effects SDK (free, developer.adobe.com) unzipped in ~/.cache/ae-plugin-deps/AfterEffectsSDK"
    warn "then:  cd rml2ae/plugin && ./build.sh install      (see rml2ae/plugin/README.md)"
  fi
fi
if want skills; then
  echo; echo "== agent skills (Claude Code): how to drive these tools"
  if [ -d "$HOME/.claude" ]; then
    mkdir -p "$HOME/.claude/skills" && cp -R "$HERE/skills/"* "$HOME/.claude/skills/" && ok "skills copied to ~/.claude/skills: $(ls "$HERE/skills" | tr '\n' ' ')"
  else warn "no ~/.claude: copy skills/* into your agent's skill folder"; fi
fi
echo; echo "Done."
