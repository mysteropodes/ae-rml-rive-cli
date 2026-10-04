// Rive Shader — After Effects SmartFX effect that runs a Rive .wgsl on a layer (source = texture 0).
#pragma once

#define RS_NAME         "Rive Shader"
#define RS_MATCH_NAME   "RIVE RiveShader"
#define RS_CATEGORY     "Rive"
#define RS_SUPPORT_URL  "https://rive.app"
#define RS_DESCRIPTION  "Runs a Rive .wgsl post-process shader on the layer (source = texture 0)."

#define RS_MAJOR_VERSION   0
#define RS_MINOR_VERSION   3
#define RS_BUG_VERSION     0
#define RS_STAGE_VERSION   PF_Stage_DEVELOP
#define RS_BUILD_VERSION   1

// Global out flags — MUST match AE_Effect_Global_OutFlags(_2) in RiveShaderPiPL.r (build.sh derives them from here).
// NON_PARAM_VARY: the output depends on time (tick / fxTick clocks) even when no parameter is animated.
// SEND_UPDATE_PARAMS_UI: we rename the generic sliders after the loaded shader's `struct Params`.
#define RS_OUT_FLAGS   (PF_OutFlag_DEEP_COLOR_AWARE | PF_OutFlag_NON_PARAM_VARY | PF_OutFlag_SEND_UPDATE_PARAMS_UI)
// SUPPORTS_THREADED_RENDERING (multi-frame rendering): reviewed 2026-09-17 — no sequence_data, every shared object
// (wgpu device + pipeline cache, registry/source cache, status map, log) is behind a mutex or immutable, render-time
// state is local to the call; GPU work itself is serialised by Gpu::mutex_.
#define RS_OUT_FLAGS2  (PF_OutFlag2_SUPPORTS_SMART_RENDER | PF_OutFlag2_FLOAT_COLOR_AWARE | PF_OutFlag2_SUPPORTS_THREADED_RENDERING)

#define RS_NUM_SLIDERS 16
#define RS_NUM_COLORS 4
#define RS_NUM_POINTS 4
#define RS_NUM_TEXTURES 3

// Parameter array indices (order in the Effect Controls panel).
enum {
    RS_PARAM_INPUT = 0,
    RS_PARAM_SHADER,        // "Shader" — registry id (integer), 0 = pass-through
    RS_PARAM_LOAD,          // "Load .wgsl…" button (supervised)
    RS_PARAM_STEP_FPS,      // tick clock, 0 = every frame
    RS_PARAM_FX_FPS,        // fxTick clock, 0 = same as tick
    RS_PARAM_SEED,
    RS_PARAM_P1,            // 16 generic float sliders, renamed after the shader's Params fields
    RS_PARAM_P16 = RS_PARAM_P1 + RS_NUM_SLIDERS - 1,
    RS_PARAM_C1,            // 4 colors: vec3/vec4 fields whose comment mentions "color"
    RS_PARAM_C4 = RS_PARAM_C1 + RS_NUM_COLORS - 1,
    RS_PARAM_PT1,           // 4 points (layer pixels): vec2 fields
    RS_PARAM_PT4 = RS_PARAM_PT1 + RS_NUM_POINTS - 1,
    RS_PARAM_TEX1,          // 3 layer params: textures at @binding(3+), in binding order
    RS_PARAM_TEX3 = RS_PARAM_TEX1 + RS_NUM_TEXTURES - 1,
    RS_NUM_PARAMS
};

// Disk ids: never reorder, only append.
enum {
    RS_ID_SHADER = 1,
    RS_ID_LOAD = 2,
    RS_ID_STEP_FPS = 3,
    RS_ID_FX_FPS = 4,
    RS_ID_SEED = 5,
    RS_ID_P1 = 10,   // .. 25
    RS_ID_C1 = 30,   // .. 33
    RS_ID_PT1 = 40,  // .. 43
    RS_ID_TEX1 = 50  // .. 52
};

enum {
    RS_CHECKOUT_PROBE = 1,   // empty request: learn the input's max_result_rect
    RS_CHECKOUT_INPUT = 2,   // the real pixels
    RS_CHECKOUT_TEX1 = 10    // .. 12: the texture layers
};
