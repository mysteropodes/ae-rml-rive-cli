#!/bin/zsh
# Build Rive Shader.plugin (arm64), compile its PiPL with Rez, ad-hoc sign, and stage it in the dev plug-ins folder.
#   ./build.sh            configure + build + stage
#   ./build.sh clean      wipe the build dir first
# Deps (outside the synced drive):
#   ~/.cache/ae-plugin-deps/AfterEffectsSDK   Adobe SDK (Examples/Headers, Examples/Resources, Examples/Util)
#   ~/.cache/ae-plugin-deps/wgpu-native       wgpu-native release (include/webgpu, lib/)
# Stage dir: ~/AE-Dev-Plugins — AE follows aliases/symlinks in its Plug-ins folder, so one-time (admin):
#   sudo ln -s ~/AE-Dev-Plugins "/Applications/Adobe After Effects 2026/Plug-ins/RiveDev"
set -euo pipefail
HERE="${0:A:h}"
DEPS="$HOME/.cache/ae-plugin-deps"
AE_SDK="${AE_SDK:-$DEPS/AfterEffectsSDK}"
BUILD="$HOME/.cache/ae-plugin-deps/build-RiveShader"     # build outside the repository
STAGE="$HOME/AE-Dev-Plugins"
[[ "${1:-}" == "clean" ]] && rm -rf "$BUILD"
# 2026-10-04: the Command Line Tools SDK 27 ships .tbd stubs (arm64e.x1) that Xcode's ld-1267 rejects -> link with
# Xcode's own SDK when it is there
XCODE_SDK=/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk
[[ -z "${SDKROOT:-}" && -d "$XCODE_SDK" ]] && export SDKROOT="$XCODE_SDK"
mkdir -p "$BUILD" "$STAGE"

cmake -S "$HERE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DAE_SDK="$AE_SDK" >/dev/null
cmake --build "$BUILD" --target flagcheck RiveShader

# 1. PiPL: constants from the compiled header (never hand-copied), template -> .r -> Rez -> .rsrc
eval "$("$BUILD/flagcheck" | sed 's/^/export /; s/=/="/; s/$/"/')"
PIPL_R="$BUILD/RiveShaderPiPL.r"
sed -e "s|@RS_NAME@|$RS_NAME|; s|@RS_MATCH_NAME@|$RS_MATCH_NAME|; s|@RS_CATEGORY@|$RS_CATEGORY|; s|@RS_SUPPORT_URL@|$RS_SUPPORT_URL|" \
    -e "s|@RS_VERSION@|$RS_VERSION|; s|@RS_OUT_FLAGS@|$RS_OUT_FLAGS|; s|@RS_OUT_FLAGS2@|$RS_OUT_FLAGS2|" \
    "$HERE/src/RiveShaderPiPL.r.in" > "$PIPL_R"
PLUGIN="$BUILD/RiveShader.plugin"
mkdir -p "$PLUGIN/Contents/Resources"
Rez -useDF -d __MACH__ \
    -i "$AE_SDK/Examples/Headers" -i "$AE_SDK/Examples/Resources" -i "$AE_SDK/Examples/Util" \
    -o "$PLUGIN/Contents/Resources/RiveShader.rsrc" "$PIPL_R"
echo -n "eFKTFXTC" > "$PLUGIN/Contents/PkgInfo"
echo "PiPL: out_flags=$RS_OUT_FLAGS out_flags2=$RS_OUT_FLAGS2 version=$RS_VERSION match='$RS_MATCH_NAME'"

# 2. signature: ad hoc for local testing; SIGN_IDENTITY="Developer ID Application: …" for distribution
#    (then: ./build.sh dist -> zip, `xcrun notarytool submit <zip> --keychain-profile <p> --wait`, `xcrun stapler staple`)
if [[ -n "${SIGN_IDENTITY:-}" ]]; then
    codesign --force --sign "$SIGN_IDENTITY" --options runtime --timestamp "$PLUGIN"
else
    codesign --force --sign - --timestamp=none "$PLUGIN" >/dev/null 2>&1
fi
codesign -dv "$PLUGIN" 2>&1 | grep -E "Format|Signature=|Authority=" | head -3 | sed 's/^/   /'

# 3. stage
rm -rf "$STAGE/RiveShader.plugin"
cp -R "$PLUGIN" "$STAGE/RiveShader.plugin"
echo "staged: $STAGE/RiveShader.plugin  (restart After Effects to load it)"

# 3b. install: AE 26.5 loads Plug-Ins/RiveShader.plugin and no longer scans the RiveDev link (2026-10-04) —
#     `./build.sh install` copies the build over the installed bundle (quit After Effects first)
if [[ "${1:-}" == "install" ]]; then
    AE_PLUG="/Applications/Adobe After Effects 2026/Plug-Ins/RiveShader.plugin"
    if pgrep -f "MacOS/After Effects$" >/dev/null; then
        echo "install: quit After Effects first (the plugin is loaded)"; exit 1
    fi
    mkdir -p "$AE_PLUG" && rsync -a --delete "$PLUGIN/" "$AE_PLUG/" && echo "installed: $AE_PLUG"
    # a second copy in the staging folder (scanned again through the RiveDev link) would load the effect twice
    rm -rf "$STAGE/RiveShader.plugin"
fi

# 4. optional distributable zip
if [[ "${1:-}" == "dist" ]]; then
    DIST="$DEPS/dist"; mkdir -p "$DIST"
    ZIP="$DIST/RiveShader-$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$PLUGIN/Contents/Info.plist").zip"
    rm -f "$ZIP"; ditto -c -k --keepParent "$PLUGIN" "$ZIP"; echo "dist: $ZIP"
fi
