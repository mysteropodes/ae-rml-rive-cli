#!/bin/bash
# rml2ae installer (macOS). Run it from anywhere:  bash <this folder>/install.sh
# What it does, in order (each step says what it found; nothing is silent):
#   1. Python 3.9+ -> a virtual env next to the package (<root>/.venv) with pillow, numpy, fontTools
#   2. Rive CLI (`rive`) -> offers `brew install --cask rive-app/tap/rive-cli`, or a direct download from
#      releases.rive.app (the public manifest + sha256 the cask itself uses) into ~/.rive/bin, if missing
#   3. ffmpeg (optional, for `ae render --out x.mp4`)
#   4. the `ae` command on your PATH (symlink in /usr/local/bin, or a line to add to your shell)
#   5. the After Effects panel (Window > Rive.jsx): copied into the AE ScriptUI Panels folder (asks for your password)
#   6. the Rive Shader plugin, if a built copy is found (copied into the AE Plug-ins folder, asks for your password)
#   7. `ae doctor` at the end
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"        # .../rml2ae
ROOT="$(dirname "$HERE")"                    # the folder that contains rml2ae/
ok()   { printf '  \033[32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m[--]\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m[!!]\033[0m %s\n' "$*"; }
ask()  { local a; read -r -p "  $1 [y/N] " a; [ "$a" = "o" ] || [ "$a" = "O" ] || [ "$a" = "y" ] || [ "$a" = "Y" ]; }

echo "rml2ae — installing in $ROOT"
echo
echo "1. Python"
PYBIN=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PYBIN="$(command -v "$c")"; break; fi
done
if [ -z "$PYBIN" ]; then fail "Python 3.9+ not found: install the Xcode Command Line Tools (xcode-select --install) or brew install python"; exit 1; fi
ok "$($PYBIN --version) ($PYBIN)"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  "$PYBIN" -m venv "$ROOT/.venv" || { fail "could not create the venv"; exit 1; }
fi
"$ROOT/.venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1
if "$ROOT/.venv/bin/pip" install -q pillow numpy fonttools py-aep; then ok "Python dependencies (pillow, numpy, fontTools, py-aep) in $ROOT/.venv"; else fail "pip failed (network?)"; fi

echo
echo "2. Rive CLI"
if command -v rive >/dev/null 2>&1; then ok "$(rive --version 2>/dev/null | head -1) ($(command -v rive))"
elif [ -x "$HOME/.rive/bin/rive" ]; then ok "rive in ~/.rive/bin (add ~/.rive/bin to PATH if 'rive' is not found)"
else
  warn "rive not found"
  # the CLI is not redistributed here: Rive publishes it at releases.rive.app (the Homebrew cask downloads the same
  # archive); the manifest lists the current version + sha256 per platform, so we can fetch it directly without brew
  rive_direct() {
    local man url sum arch tmp
    case "$(uname -s)-$(uname -m)" in
      Darwin-arm64) arch=darwin-arm64 ;; Linux-x86_64) arch=linux-x64 ;;
      *) warn "no official binary for $(uname -s)-$(uname -m)"; return 1 ;;
    esac
    man="$(curl -fsSL https://releases.rive.app/cli/latest/manifest.json)" || { fail "manifest unreachable"; return 1; }
    url="https://releases.rive.app/cli/$(printf '%s' "$man" | "$PYBIN" -c "import json,sys; print(json.load(sys.stdin)['artifacts']['$arch']['path'])")"
    sum="$(printf '%s' "$man" | "$PYBIN" -c "import json,sys; print(json.load(sys.stdin)['artifacts']['$arch']['sha256'])")"
    tmp="$(mktemp -d)"
    curl -fsSL "$url" -o "$tmp/rive.tar.gz" || { fail "download failed: $url"; return 1; }
    [ "$(shasum -a 256 "$tmp/rive.tar.gz" | cut -d' ' -f1)" = "$sum" ] || { fail "sha256 differs from the manifest, archive ignored"; return 1; }
    mkdir -p "$HOME/.rive/bin" && tar -xzf "$tmp/rive.tar.gz" -C "$tmp" && \
      install -m 755 "$(find "$tmp" -type f -name rive | head -1)" "$HOME/.rive/bin/rive" || { fail "extraction failed"; return 1; }
    rm -rf "$tmp"
    ok "$("$HOME/.rive/bin/rive" --version 2>/dev/null | head -1) dans ~/.rive/bin"
    case ":$PATH:" in *":$HOME/.rive/bin:"*) ;; *) warn "add to your shell:  export PATH=\"\$HOME/.rive/bin:\$PATH\"" ;; esac
  }
  if command -v brew >/dev/null 2>&1 && ask "install it with Homebrew (brew install --cask rive-app/tap/rive-cli)?"; then
    brew install --cask rive-app/tap/rive-cli && ok "rive installed" || fail "brew failed: see https://rive.app (CLI)"
  elif ask "download it directly from releases.rive.app (sha256 verified) into ~/.rive/bin?"; then
    rive_direct || warn "install it by hand: brew install --cask rive-app/tap/rive-cli (or the rive.app installer)"
  else warn "to install: brew install --cask rive-app/tap/rive-cli  (or the rive.app installer) — without it, no Luau replay and no verification"; fi
fi

echo
echo "3. ffmpeg (optional)"
if command -v ffmpeg >/dev/null 2>&1; then ok "ffmpeg"; else warn "ffmpeg missing: 'ae render --out x.mp4' unavailable (brew install ffmpeg)"; fi

echo
echo "4. the ae command"
chmod +x "$HERE/bin/ae"
if [ -w /usr/local/bin ] || [ ! -e /usr/local/bin ]; then
  mkdir -p /usr/local/bin 2>/dev/null && ln -sf "$HERE/bin/ae" /usr/local/bin/ae && ok "ae -> /usr/local/bin/ae"
elif ask "create /usr/local/bin/ae (admin password)?"; then
  sudo ln -sf "$HERE/bin/ae" /usr/local/bin/ae && ok "ae -> /usr/local/bin/ae"
else warn "add to your shell:  export PATH=\"$HERE/bin:\$PATH\""; fi

echo
echo "5. the After Effects panel"
AEAPP="$(ls -d "/Applications/Adobe After Effects "* 2>/dev/null | sort | tail -1)"
if [ -z "$AEAPP" ]; then warn "After Effects not found in /Applications: panel and plugin not installed"
else
  ok "After Effects : $AEAPP"
  PANEL_DIR="$AEAPP/Scripts/ScriptUI Panels"
  TMP="$(mktemp)"
  sed -e "s|^  var ROOT = \"[^\"]*\";|  var ROOT = \"$ROOT\";|" -e "s|^  var PY = .*|  var PY = ROOT + \"/.venv/bin/python\";|" "$HERE/panel/Rive.jsx" > "$TMP"
  # install -m 644: a plain `cp` of the mktemp file keeps mode 600 root → AE cannot read it (measured)
  if [ -w "$PANEL_DIR" ]; then install -m 644 "$TMP" "$PANEL_DIR/Rive.jsx" && ok "panel copied: Window > Rive.jsx (restart AE)"
  elif ask "copy the panel into \"$PANEL_DIR\" (admin password)?"; then
    sudo install -m 644 "$TMP" "$PANEL_DIR/Rive.jsx" && ok "panel copied: Window > Rive.jsx (restart AE)"
  else warn "do it by hand: sudo cp \"$HERE/panel/Rive.jsx\" \"$PANEL_DIR/\""; fi
  rm -f "$TMP"
  echo "     In AE: Preferences > Scripting & Expressions > check \"Allow Scripts to Write Files and Access Network\""

  echo
  echo "6. the Rive Shader plugin (optional: WGSL post-process shaders as they are)"
  PLUG=""
  for c in "$HERE/plugin/dist/RiveShader.plugin" "$ROOT/RiveShader.plugin" "$HOME/AE-Dev-Plugins/RiveShader.plugin"; do [ -d "$c" ] && { PLUG="$c"; break; }; done
  if [ -z "$PLUG" ]; then warn "no built RiveShader.plugin (rml2ae/plugin/build.sh): shaders go through native AE effects"
  elif [ -d "$AEAPP/Plug-ins/RiveShader.plugin" ]; then ok "plugin already present in $AEAPP/Plug-ins"
  elif ask "copy $PLUG into \"$AEAPP/Plug-ins\" (admin password)?"; then
    sudo cp -R "$PLUG" "$AEAPP/Plug-ins/" && ok "plugin copied (if macOS blocks it: Settings > Privacy & Security > Allow)"
  else warn "plugin not copied"; fi
fi

echo
echo "7. the Claude Code skill (optional: the agent knows ae, its rules and what the conversion produces)"
if [ -d "$HOME/.claude" ]; then
  if ask "copy skill/rml2ae into ~/.claude/skills/?"; then mkdir -p "$HOME/.claude/skills" && cp -R "$HERE/skill/rml2ae" "$HOME/.claude/skills/" && ok "rml2ae skill installed (see LLM_SETUP.md for the project CLAUDE.md)"; else warn "skill not copied (LLM_SETUP.md)"; fi
else warn "no ~/.claude: see LLM_SETUP.md for your agent"; fi

echo
echo "8. verification"
"$HERE/bin/ae" doctor 2>&1 | sed 's/^/  /'
echo
echo "Done. Try it:  ae build <Rive CLI project folder>   (After Effects open)   — docs: $HERE/README.md, $HERE/INSTALL.md"
