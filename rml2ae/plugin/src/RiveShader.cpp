// Rive Shader — After Effects SmartFX effect running a Rive .wgsl through wgpu-native (Metal).
//
// SDK rules followed (ae-plugins.docsforadobe.dev):
//  - SmartFX: PRE_RENDER checks the input out (one id per use), RENDER checks pixels out once per id, then the
//    output, then checks them in. Worlds traversed by rowbytes only. Format via PF_WorldSuite2::PF_GetPixelFormat.
//  - pre_render_data is handed to AE with its delete function (AE owns it afterwards).
//  - No PF_OutFlag2_SUPPORTS_THREADED_RENDERING: the GPU module serialises renders with a mutex anyway.
//  - Never a modal: errors go to the log file + the "Shader" parameter's name; the image falls back to a copy.
//  - No C++ exception may escape the C entry point (Apple Silicon ABI -> terminate()).
#include "AEConfig.h"
#include "entry.h"
#include "AE_Effect.h"
#include "AE_EffectCB.h"
#include "AE_EffectCBSuites.h"
#include "AE_EffectSuites.h"
#include "AE_Macros.h"
#include "AE_GeneralPlug.h"
#include "AE_EffectPixelFormat.h"
#include "Param_Utils.h"
#include "RiveShader.h"
#include "RsGpu.h"
#include "RsRegistry.h"
#include "RsDialog.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

// ---------------------------------------------------------------------------------------------- process state
namespace {

std::mutex g_gpuMutex;
std::unique_ptr<rs::Gpu> g_gpu;
std::string g_gpuError;
bool g_gpuTried = false;

// Last status per shader id, shown in the Effect Controls (never as a dialog).
std::mutex g_statusMutex;
std::map<int, std::string> g_status;

void setStatus(int id, const std::string& s) {
    std::lock_guard<std::mutex> lk(g_statusMutex);
    if (g_status[id] != s) {
        g_status[id] = s;
        if (!s.empty()) rs::logLine("shader " + std::to_string(id) + ": " + s);
    }
}
std::string getStatus(int id) {
    std::lock_guard<std::mutex> lk(g_statusMutex);
    auto it = g_status.find(id);
    return it == g_status.end() ? std::string() : it->second;
}

rs::Gpu* gpu() {
    std::lock_guard<std::mutex> lk(g_gpuMutex);
    if (!g_gpu && !g_gpuTried) {
        g_gpuTried = true;
        g_gpu = rs::Gpu::create(g_gpuError);
        rs::logLine(g_gpu ? "wgpu device ready: " + g_gpu->adapterName() + (g_gpu->float32Filterable() ? " (float32-filterable)" : "")
                          : "wgpu device FAILED: " + g_gpuError);
    }
    return g_gpu.get();
}

struct PreRenderData {
    PF_LRect inRect;    // where the input pixels land (layer coords)
    PF_LRect outRect;   // our result rect (layer coords)
    PF_LRect layerRect; // the shader's frame: the layer itself (0,0,w,h at the current downsample), whatever the
                        // effects above did to the buffer (grown by Drop Shadow / Glow / blur, shrunk by a mask)
    bool texChecked[RS_NUM_TEXTURES];   // texture layer i was checked out (pixels may still be NULL)
};
void DeletePreRenderData(void* p) { delete static_cast<PreRenderData*>(p); }

// Rive convention -> effect parameters. Fields of `struct Params` not filled by the host, in declaration order:
//   vec2                          -> a Point (layer pixels)          while points are left
//   vec3 / vec4 with "color" in the comment -> a Color              while colors are left
//   anything else (one scalar at a time)    -> the generic sliders  (vec2 = 2 sliders, ...)
// Textures at @binding(3+) -> the Layer params "Texture 1..3", in binding order.
enum class Slot { None, Host, Slider, Color, Point };
struct ParamMap {
    std::vector<Slot> slot;      // per param field
    std::vector<int> index;      // slot index (first slider for Slider)
    int sliders = 0, colors = 0, points = 0;
    std::vector<int> textureBinding;   // Texture i -> binding number of the (i+1)-th extra texture
    std::vector<std::string> textureName;
};
bool wantsColor(const rs::ParamField& f) {
    if (f.count < 3) return false;
    std::string c = f.comment;
    for (auto& ch : c) ch = (char)tolower((unsigned char)ch);
    return c.find("color") != std::string::npos || c.find("colour") != std::string::npos || c.find("couleur") != std::string::npos;
}
ParamMap mapParams(const rs::ShaderInfo& info) {
    ParamMap m;
    for (const auto& f : info.params) {
        if (rs::isHostField(f.name)) { m.slot.push_back(Slot::Host); m.index.push_back(-1); continue; }
        if (f.count == 2 && !f.integer && m.points < RS_NUM_POINTS) { m.slot.push_back(Slot::Point); m.index.push_back(m.points++); continue; }
        if (wantsColor(f) && m.colors < RS_NUM_COLORS) { m.slot.push_back(Slot::Color); m.index.push_back(m.colors++); continue; }
        if (m.sliders + f.count <= RS_NUM_SLIDERS) { m.slot.push_back(Slot::Slider); m.index.push_back(m.sliders); m.sliders += f.count; continue; }
        m.slot.push_back(Slot::None); m.index.push_back(-1);
    }
    bool first = true;
    for (const auto& b : info.bindings) {
        if (b.kind != rs::BindKind::Texture) continue;
        if (first) { first = false; continue; }
        if ((int)m.textureBinding.size() < RS_NUM_TEXTURES) { m.textureBinding.push_back(b.binding); m.textureName.push_back(b.name); }
    }
    return m;
}
std::shared_ptr<const rs::Source> sourceForId(int id) {
    if (id <= 0) return nullptr;
    std::string path = rs::resolveShader(id);
    return path.empty() ? nullptr : rs::loadSource(path);
}

}  // namespace (anon)

// ---------------------------------------------------------------------------------------------- selectors
static PF_Err About(PF_InData* in_data, PF_OutData* out_data) {
    std::snprintf(out_data->return_msg, sizeof(out_data->return_msg), "%s v%d.%d.%d\r%s", RS_NAME,
                  RS_MAJOR_VERSION, RS_MINOR_VERSION, RS_BUG_VERSION, RS_DESCRIPTION);
    return PF_Err_NONE;
}

static PF_Err GlobalSetup(PF_InData* in_data, PF_OutData* out_data) {
    out_data->my_version = PF_VERSION(RS_MAJOR_VERSION, RS_MINOR_VERSION, RS_BUG_VERSION, RS_STAGE_VERSION, RS_BUILD_VERSION);
    out_data->out_flags = RS_OUT_FLAGS;
    out_data->out_flags2 = RS_OUT_FLAGS2;
    return PF_Err_NONE;
}

static PF_Err GlobalSetdown(PF_InData* in_data, PF_OutData* out_data) {
    std::lock_guard<std::mutex> lk(g_gpuMutex);
    g_gpu.reset();
    return PF_Err_NONE;
}

static PF_Err ParamsSetup(PF_InData* in_data, PF_OutData* out_data) {
    PF_ParamDef def;
    AEFX_CLR_STRUCT(def);
    PF_ADD_FLOAT_SLIDERX("Shader", 0, 999999, 0, 100, 0, PF_Precision_INTEGER, 0, PF_ParamFlag_SUPERVISE | PF_ParamFlag_CANNOT_TIME_VARY, RS_ID_SHADER);
    AEFX_CLR_STRUCT(def);
    PF_ADD_BUTTON("", "Load .wgsl...", 0, PF_ParamFlag_SUPERVISE | PF_ParamFlag_CANNOT_TIME_VARY, RS_ID_LOAD);
    AEFX_CLR_STRUCT(def);
    PF_ADD_FLOAT_SLIDERX("Step fps (0 = every frame)", 0, 240, 0, 30, 0, PF_Precision_TENTHS, 0, 0, RS_ID_STEP_FPS);
    AEFX_CLR_STRUCT(def);
    PF_ADD_FLOAT_SLIDERX("FX fps (0 = step)", 0, 240, 0, 30, 0, PF_Precision_TENTHS, 0, 0, RS_ID_FX_FPS);
    AEFX_CLR_STRUCT(def);
    PF_ADD_FLOAT_SLIDERX("Seed", 0, 100000, 0, 100, 1, PF_Precision_INTEGER, 0, 0, RS_ID_SEED);
    for (int i = 0; i < RS_NUM_SLIDERS; ++i) {
        char name[32];
        std::snprintf(name, sizeof name, "P%d", i + 1);
        AEFX_CLR_STRUCT(def);
        PF_ADD_FLOAT_SLIDERX(name, -1000000, 1000000, 0, 1, 0, PF_Precision_THOUSANDTHS, 0, 0, RS_ID_P1 + i);
    }
    for (int i = 0; i < RS_NUM_COLORS; ++i) {
        char name[32];
        std::snprintf(name, sizeof name, "C%d", i + 1);
        AEFX_CLR_STRUCT(def);
        PF_ADD_COLOR(name, 255, 255, 255, RS_ID_C1 + i);
    }
    for (int i = 0; i < RS_NUM_POINTS; ++i) {
        char name[32];
        std::snprintf(name, sizeof name, "Pt%d", i + 1);
        AEFX_CLR_STRUCT(def);
        PF_ADD_POINT(name, 50, 50, 0, RS_ID_PT1 + i);
    }
    for (int i = 0; i < RS_NUM_TEXTURES; ++i) {
        char name[32];
        std::snprintf(name, sizeof name, "Texture %d", i + 1);
        AEFX_CLR_STRUCT(def);
        PF_ADD_LAYER(name, PF_LayerDefault_NONE, RS_ID_TEX1 + i);
    }
    out_data->num_params = RS_NUM_PARAMS;
    return PF_Err_NONE;
}

// Rename / enable the generic sliders after the loaded shader (UI thread; cosmetic only).
static PF_Err UpdateParamsUI(PF_InData* in_data, PF_OutData* out_data, PF_ParamDef* params[]) {
    PF_Err err = PF_Err_NONE;
    PF_ParamUtilsSuite3* pu = nullptr;
    SPBasicSuite* basic = in_data->pica_basicP;
    if (!basic || basic->AcquireSuite(kPFParamUtilsSuite, kPFParamUtilsSuiteVersion3, (const void**)&pu) != kSPNoError || !pu) return PF_Err_NONE;

    int id = (int)std::lround(params[RS_PARAM_SHADER]->u.fs_d.value);
    std::shared_ptr<const rs::Source> src;
    std::string path = id > 0 ? rs::resolveShader(id) : "";
    if (!path.empty()) src = rs::loadSource(path);
    std::string status = getStatus(id);
    // "Shader" label: file name or problem (PF_DEF_NAME is 31 chars)
    {
        PF_ParamDef d = *params[RS_PARAM_SHADER];
        std::string label = "Shader";
        if (id > 0 && path.empty()) label = "Shader (id unknown)";
        else if (src && !src->error.empty()) label = "Shader (bad file, see log)";
        else if (!status.empty()) label = "Shader (error, see log)";
        else if (src) {
            std::string base = path.substr(path.find_last_of('/') + 1);
            label = "Shader: " + base;
        }
        std::snprintf(d.PF_DEF_NAME, sizeof(d.PF_DEF_NAME), "%s", label.c_str());
        ERR(pu->PF_UpdateParamUI(in_data->effect_ref, RS_PARAM_SHADER, &d));
    }
    // Generic sliders / colors / points / textures renamed after the shader; unused ones greyed out
    std::vector<std::string> names(RS_NUM_SLIDERS), cnames(RS_NUM_COLORS), pnames(RS_NUM_POINTS), tnames(RS_NUM_TEXTURES);
    std::vector<double> mins(RS_NUM_SLIDERS, 0), maxs(RS_NUM_SLIDERS, 1);
    if (src && src->error.empty()) {
        ParamMap m = mapParams(src->info);
        static const char* comp[4] = {".x", ".y", ".z", ".w"};
        for (size_t i = 0; i < src->info.params.size(); ++i) {
            const auto& f = src->info.params[i];
            if (m.slot[i] == Slot::Slider) {
                for (int c = 0; c < f.count; ++c) { names[m.index[i] + c] = f.count == 1 ? f.name : f.name + comp[c]; mins[m.index[i] + c] = f.min; maxs[m.index[i] + c] = f.max; }
            } else if (m.slot[i] == Slot::Color) cnames[m.index[i]] = f.name;
            else if (m.slot[i] == Slot::Point) pnames[m.index[i]] = f.name;
        }
        for (size_t i = 0; i < m.textureName.size(); ++i) tnames[i] = m.textureName[i];
    }
    auto rename = [&](int paramIndex, const std::string& used, const char* prefix, int n, double* mn, double* mx) {
        PF_ParamDef d = *params[paramIndex];
        std::string label = !used.empty() ? used : std::string(prefix) + std::to_string(n) + " (unused)";
        std::snprintf(d.PF_DEF_NAME, sizeof(d.PF_DEF_NAME), "%s", label.c_str());
        if (mn) { d.u.fs_d.slider_min = (PF_FpShort)*mn; d.u.fs_d.slider_max = (PF_FpShort)*mx; }
        if (!used.empty()) d.ui_flags &= ~PF_PUI_DISABLED; else d.ui_flags |= PF_PUI_DISABLED;
        ERR(pu->PF_UpdateParamUI(in_data->effect_ref, paramIndex, &d));
    };
    for (int i = 0; i < RS_NUM_SLIDERS; ++i) rename(RS_PARAM_P1 + i, names[i], "P", i + 1, &mins[i], &maxs[i]);
    for (int i = 0; i < RS_NUM_COLORS; ++i) rename(RS_PARAM_C1 + i, cnames[i], "C", i + 1, nullptr, nullptr);
    for (int i = 0; i < RS_NUM_POINTS; ++i) rename(RS_PARAM_PT1 + i, pnames[i], "Pt", i + 1, nullptr, nullptr);
    for (int i = 0; i < RS_NUM_TEXTURES; ++i) rename(RS_PARAM_TEX1 + i, tnames[i], "Texture ", i + 1, nullptr, nullptr);
    basic->ReleaseSuite(kPFParamUtilsSuite, kPFParamUtilsSuiteVersion3);
    return err;
}

// Shader slider changed -> apply the shader's documented defaults; button -> file picker + registry.
static PF_Err UserChangedParam(PF_InData* in_data, PF_OutData* out_data, PF_ParamDef* params[], const PF_UserChangedParamExtra* which) {
    PF_Err err = PF_Err_NONE;
    if (which->param_index == RS_PARAM_LOAD) {
        std::string path = rs::pickWgslFile();
        if (!path.empty()) {
            int id = rs::registerShader(path);
            if (id > 0) {
                params[RS_PARAM_SHADER]->u.fs_d.value = id;
                params[RS_PARAM_SHADER]->uu.change_flags = PF_ChangeFlag_CHANGED_VALUE;
                setStatus(id, "");
                rs::logLine("registered " + std::to_string(id) + " -> " + path);
                which = nullptr;  // fall through to the defaults below
            }
        }
    }
    if (!which || which->param_index == RS_PARAM_SHADER) {
        int id = (int)std::lround(params[RS_PARAM_SHADER]->u.fs_d.value);
        std::string path = id > 0 ? rs::resolveShader(id) : "";
        if (!path.empty()) {
            auto src = rs::loadSource(path);
            if (src && src->error.empty()) {
                ParamMap m = mapParams(src->info);
                for (size_t i = 0; i < src->info.params.size(); ++i) {
                    const auto& f = src->info.params[i];
                    if (m.slot[i] != Slot::Slider || !(f.hasDefault || f.hasRange)) continue;
                    double v = f.hasDefault ? f.def : std::min(std::max(0.0, f.min), f.max);
                    for (int c = 0; c < f.count; ++c) {
                        params[RS_PARAM_P1 + m.index[i] + c]->u.fs_d.value = (PF_FpLong)v;
                        params[RS_PARAM_P1 + m.index[i] + c]->uu.change_flags = PF_ChangeFlag_CHANGED_VALUE;
                    }
                }
            }
        }
    }
    out_data->out_flags |= PF_OutFlag_REFRESH_UI;
    return err;
}

// PF_Cmd_SMART_PRE_RENDER: the shader runs on the layer rect (Rive's canvas = the layer), so it needs the input over
// the whole layer whatever AE asks for; input pixels outside the layer rect (an effect above grew the buffer) pass
// through, so an effect below still finds them.
static PF_LRect Union(const PF_LRect& a, const PF_LRect& b) {
    if (a.right <= a.left || a.bottom <= a.top) return b;
    if (b.right <= b.left || b.bottom <= b.top) return a;
    PF_LRect r;
    r.left = std::min(a.left, b.left); r.top = std::min(a.top, b.top); r.right = std::max(a.right, b.right); r.bottom = std::max(a.bottom, b.bottom);
    return r;
}
static PF_LRect Intersect(const PF_LRect& a, const PF_LRect& b) {
    PF_LRect r;
    r.left = std::max(a.left, b.left); r.top = std::max(a.top, b.top); r.right = std::min(a.right, b.right); r.bottom = std::min(a.bottom, b.bottom);
    if (r.right <= r.left || r.bottom <= r.top) r.left = r.top = r.right = r.bottom = 0;
    return r;
}
static PF_LRect LayerRect(const PF_InData* in_data) {
    PF_LRect r;
    r.left = r.top = 0;
    const double sx = in_data->downsample_x.den ? (double)in_data->downsample_x.num / in_data->downsample_x.den : 1.0;
    const double sy = in_data->downsample_y.den ? (double)in_data->downsample_y.num / in_data->downsample_y.den : 1.0;
    r.right = (A_long)std::ceil(in_data->width * sx - 1e-6);
    r.bottom = (A_long)std::ceil(in_data->height * sy - 1e-6);
    return r;
}

static PF_Err PreRender(PF_InData* in_data, PF_OutData* out_data, PF_PreRenderExtra* extra) {
    PF_Err err = PF_Err_NONE;
    PF_RenderRequest req = extra->input->output_request;
    PF_CheckoutResult probe, in_result;
    std::memset(&probe, 0, sizeof probe);
    std::memset(&in_result, 0, sizeof in_result);
    PF_RenderRequest empty = req;
    empty.rect.left = empty.rect.top = empty.rect.right = empty.rect.bottom = 0;
    ERR(extra->cb->checkout_layer(in_data->effect_ref, RS_PARAM_INPUT, RS_CHECKOUT_PROBE, &empty,
                                  in_data->current_time, in_data->time_step, in_data->time_scale, &probe));
    PF_LRect layer = LayerRect(in_data);
    if (layer.right <= 0 || layer.bottom <= 0) layer = probe.max_result_rect;   // no layer size: old behaviour
    {
        static std::mutex m;
        static std::string last;
        char b[256];
        std::snprintf(b, sizeof b, "prerender layer %dx%d (in_data %dx%d ds %d/%d) input max [%d,%d,%d,%d] request [%d,%d,%d,%d]",
                      (int)layer.right, (int)layer.bottom, (int)in_data->width, (int)in_data->height,
                      (int)in_data->downsample_x.num, (int)in_data->downsample_x.den,
                      (int)probe.max_result_rect.left, (int)probe.max_result_rect.top, (int)probe.max_result_rect.right, (int)probe.max_result_rect.bottom,
                      (int)req.rect.left, (int)req.rect.top, (int)req.rect.right, (int)req.rect.bottom);
        std::lock_guard<std::mutex> lk(m);
        if (last != b) { last = b; rs::logLine(b); }
    }
    if (!err) {
        // the whole layer (shader neighbourhood) + what AE asks for (pass-through), within what the input can produce
        req.rect = Intersect(Union(layer, req.rect), probe.max_result_rect);
        ERR(extra->cb->checkout_layer(in_data->effect_ref, RS_PARAM_INPUT, RS_CHECKOUT_INPUT, &req,
                                      in_data->current_time, in_data->time_step, in_data->time_scale, &in_result));
    }
    std::unique_ptr<PreRenderData> prd(new PreRenderData());
    std::memset(prd->texChecked, 0, sizeof prd->texChecked);
    if (!err) {
        // Extra textures: the shader's @binding(3+) textures, one Layer param each (full frame of that layer).
        PF_ParamDef sh;
        AEFX_CLR_STRUCT(sh);
        int id = 0;
        if (PF_CHECKOUT_PARAM(in_data, RS_PARAM_SHADER, in_data->current_time, in_data->time_step, in_data->time_scale, &sh) == PF_Err_NONE) {
            id = (int)std::lround(sh.u.fs_d.value);
            PF_CHECKIN_PARAM(in_data, &sh);
        }
        auto src = sourceForId(id);
        if (src && src->error.empty()) {
            ParamMap m = mapParams(src->info);
            for (size_t i = 0; i < m.textureBinding.size() && !err; ++i) {
                PF_CheckoutResult tp, tr;
                std::memset(&tp, 0, sizeof tp);
                std::memset(&tr, 0, sizeof tr);
                PF_RenderRequest treq = extra->input->output_request;
                treq.rect.left = treq.rect.top = treq.rect.right = treq.rect.bottom = 0;
                ERR(extra->cb->checkout_layer(in_data->effect_ref, RS_PARAM_TEX1 + (int)i, RS_CHECKOUT_TEX1 + 10 + (int)i, &treq,
                                              in_data->current_time, in_data->time_step, in_data->time_scale, &tp));
                if (!err) {
                    treq.rect = tp.max_result_rect;
                    ERR(extra->cb->checkout_layer(in_data->effect_ref, RS_PARAM_TEX1 + (int)i, RS_CHECKOUT_TEX1 + (int)i, &treq,
                                                  in_data->current_time, in_data->time_step, in_data->time_scale, &tr));
                    if (!err) prd->texChecked[i] = true;
                }
            }
        }
    }
    if (!err) {
        prd->inRect = in_result.result_rect;
        prd->layerRect = layer;
        prd->outRect = Union(layer, in_result.result_rect);
        extra->output->result_rect = prd->outRect;
        extra->output->max_result_rect = Union(layer, probe.max_result_rect);
        extra->output->solid = FALSE;
        extra->output->pre_render_data = prd.release();
        extra->output->delete_pre_render_data_func = DeletePreRenderData;
    }
    return err;
}

// ---------------------------------------------------------------------------------------------- pixels
static A_long BytesPerPixel(PF_PixelFormat f) {
    switch (f) {
        case PF_PixelFormat_ARGB32: return 4;
        case PF_PixelFormat_ARGB64: return 8;
        case PF_PixelFormat_ARGB128: return 16;
        default: return 0;
    }
}

static PF_Err CopyWorld(const PF_EffectWorld* in, PF_EffectWorld* out, PF_PixelFormat fmt, int dx, int dy) {
    const A_long bpp = BytesPerPixel(fmt);
    if (bpp == 0 || !in->data || !out->data) return PF_Err_BAD_CALLBACK_PARAM;
    for (A_long y = 0; y < out->height; ++y) {
        char* drow = reinterpret_cast<char*>(out->data) + (size_t)y * out->rowbytes;
        std::memset(drow, 0, (size_t)out->width * bpp);
        A_long sy = y - dy;
        if (sy < 0 || sy >= in->height) continue;
        A_long x0 = std::max<A_long>(0, dx), x1 = std::min<A_long>(out->width, dx + in->width);
        if (x1 > x0)
            std::memcpy(drow + (size_t)x0 * bpp, reinterpret_cast<const char*>(in->data) + (size_t)sy * in->rowbytes + (size_t)(x0 - dx) * bpp, (size_t)(x1 - x0) * bpp);
    }
    return PF_Err_NONE;
}

// AE world (straight alpha, A,R,G,B) -> premultiplied RGBA texels at (dx,dy) inside a w x h buffer.
static void WorldToTexels(const PF_EffectWorld* in, PF_PixelFormat fmt, int w, int h, int dx, int dy, rs::PixelFmt pf, std::vector<uint8_t>& buf) {
    const size_t tsz = pf == rs::PixelFmt::RGBA8 ? 4 : 16;
    buf.assign((size_t)w * h * tsz, 0);
    for (A_long sy = 0; sy < in->height; ++sy) {
        int y = sy + dy;
        if (y < 0 || y >= h) continue;
        const char* srow = reinterpret_cast<const char*>(in->data) + (size_t)sy * in->rowbytes;
        for (A_long sx = 0; sx < in->width; ++sx) {
            int x = sx + dx;
            if (x < 0 || x >= w) continue;
            uint8_t* d = buf.data() + ((size_t)y * w + x) * tsz;
            if (fmt == PF_PixelFormat_ARGB32) {
                const PF_Pixel8* p = reinterpret_cast<const PF_Pixel8*>(srow) + sx;
                if (pf == rs::PixelFmt::RGBA8) {
                    unsigned a = p->alpha;
                    if (a == 255) { d[0] = p->red; d[1] = p->green; d[2] = p->blue; d[3] = 255; }
                    else { d[0] = (uint8_t)((p->red * a + 127) / 255); d[1] = (uint8_t)((p->green * a + 127) / 255); d[2] = (uint8_t)((p->blue * a + 127) / 255); d[3] = (uint8_t)a; }
                } else {
                    float a = p->alpha / 255.f, v[4] = {p->red / 255.f * a, p->green / 255.f * a, p->blue / 255.f * a, a};
                    std::memcpy(d, v, 16);
                }
            } else if (fmt == PF_PixelFormat_ARGB64) {
                const PF_Pixel16* p = reinterpret_cast<const PF_Pixel16*>(srow) + sx;
                float a = p->alpha / (float)PF_MAX_CHAN16;
                float v[4] = {p->red / (float)PF_MAX_CHAN16 * a, p->green / (float)PF_MAX_CHAN16 * a, p->blue / (float)PF_MAX_CHAN16 * a, a};
                std::memcpy(d, v, 16);
            } else {
                const PF_PixelFloat* p = reinterpret_cast<const PF_PixelFloat*>(srow) + sx;
                float a = p->alpha, v[4] = {p->red * a, p->green * a, p->blue * a, a};
                std::memcpy(d, v, 16);
            }
        }
    }
}

// Premultiplied RGBA texels -> AE world (straight alpha).
static void TexelsToWorld(const std::vector<uint8_t>& buf, int w, int h, int ox, int oy, rs::PixelFmt pf, PF_EffectWorld* out, PF_PixelFormat fmt) {
    const size_t tsz = pf == rs::PixelFmt::RGBA8 ? 4 : 16;
    for (A_long ty = 0; ty < h; ++ty) {
        const A_long y = ty + oy;
        if (y < 0 || y >= out->height) continue;
        char* drow = reinterpret_cast<char*>(out->data) + (size_t)y * out->rowbytes;
        for (A_long tx = 0; tx < w; ++tx) {
            const A_long x = tx + ox;
            if (x < 0 || x >= out->width) continue;
            const uint8_t* s = buf.data() + ((size_t)ty * w + tx) * tsz;
            if (fmt == PF_PixelFormat_ARGB32) {
                PF_Pixel8* p = reinterpret_cast<PF_Pixel8*>(drow) + x;
                if (pf == rs::PixelFmt::RGBA8) {
                    unsigned a = s[3];
                    if (a == 255 || a == 0) { p->red = s[0]; p->green = s[1]; p->blue = s[2]; p->alpha = (A_u_char)a; }
                    else { p->red = (A_u_char)std::min(255u, (s[0] * 255u + a / 2) / a); p->green = (A_u_char)std::min(255u, (s[1] * 255u + a / 2) / a); p->blue = (A_u_char)std::min(255u, (s[2] * 255u + a / 2) / a); p->alpha = (A_u_char)a; }
                } else {
                    float v[4]; std::memcpy(v, s, 16);
                    float a = std::min(1.f, std::max(0.f, v[3])), inv = a > 0 ? 1.f / a : 0.f;
                    auto q = [](float f) { return (A_u_char)std::lround(std::min(1.f, std::max(0.f, f)) * 255.f); };
                    p->red = q(v[0] * inv); p->green = q(v[1] * inv); p->blue = q(v[2] * inv); p->alpha = q(a);
                }
            } else if (fmt == PF_PixelFormat_ARGB64) {
                PF_Pixel16* p = reinterpret_cast<PF_Pixel16*>(drow) + x;
                float v[4]; std::memcpy(v, s, 16);
                float a = std::min(1.f, std::max(0.f, v[3])), inv = a > 0 ? 1.f / a : 0.f;
                auto q = [](float f) { return (A_u_short)std::lround(std::min(1.f, std::max(0.f, f)) * PF_MAX_CHAN16); };
                p->red = q(v[0] * inv); p->green = q(v[1] * inv); p->blue = q(v[2] * inv); p->alpha = q(a);
            } else {
                PF_PixelFloat* p = reinterpret_cast<PF_PixelFloat*>(drow) + x;
                float v[4]; std::memcpy(v, s, 16);
                float a = v[3], inv = a > 0 ? 1.f / a : 0.f;
                p->red = v[0] * inv; p->green = v[1] * inv; p->blue = v[2] * inv; p->alpha = a;
            }
        }
    }
}

// ---------------------------------------------------------------------------------------------- render
static PF_Err SmartRender(PF_InData* in_data, PF_OutData* out_data, PF_SmartRenderExtra* extra) {
    PF_Err err = PF_Err_NONE, err2 = PF_Err_NONE;
    const PreRenderData* prd = static_cast<const PreRenderData*>(extra->input->pre_render_data);
    PF_EffectWorld* inW = nullptr;
    PF_EffectWorld* outW = nullptr;

    // Non-layer parameters (SmartFX gets no param array)
    double pv[RS_NUM_PARAMS] = {0};
    double colors[RS_NUM_COLORS][4] = {{0}}, points[RS_NUM_POINTS][2] = {{0}};
    for (int i = RS_PARAM_SHADER; i < RS_PARAM_TEX1; ++i) {
        if (i == RS_PARAM_LOAD) continue;
        PF_ParamDef def;
        AEFX_CLR_STRUCT(def);
        ERR(PF_CHECKOUT_PARAM(in_data, i, in_data->current_time, in_data->time_step, in_data->time_scale, &def));
        if (err) break;
        if (i >= RS_PARAM_C1 && i <= RS_PARAM_C4) {
            double* c = colors[i - RS_PARAM_C1];
            c[0] = def.u.cd.value.red / 255.0; c[1] = def.u.cd.value.green / 255.0; c[2] = def.u.cd.value.blue / 255.0; c[3] = 1.0;
        } else if (i >= RS_PARAM_PT1 && i <= RS_PARAM_PT4) {
            double* pt = points[i - RS_PARAM_PT1];
            pt[0] = def.u.td.x_value / 65536.0; pt[1] = def.u.td.y_value / 65536.0;   // layer pixels, downsample-adjusted
        } else {
            pv[i] = def.u.fs_d.value;
        }
        ERR2(PF_CHECKIN_PARAM(in_data, &def));
    }
    const int shaderId = (int)std::lround(pv[RS_PARAM_SHADER]);
    PF_EffectWorld* texW[RS_NUM_TEXTURES] = {nullptr, nullptr, nullptr};

    ERR(extra->cb->checkout_layer_pixels(in_data->effect_ref, RS_CHECKOUT_INPUT, &inW));
    if (!err && inW) ERR(extra->cb->checkout_output(in_data->effect_ref, &outW));
    if (!err && inW && outW && prd) {
        PF_PixelFormat fmt = PF_PixelFormat_INVALID;
        PF_WorldSuite2* wsP = nullptr;
        SPBasicSuite* basic = in_data->pica_basicP;
        if (!basic || basic->AcquireSuite(kPFWorldSuite, kPFWorldSuiteVersion2, (const void**)&wsP) != kSPNoError || !wsP) {
            err = PF_Err_BAD_CALLBACK_PARAM;
            wsP = nullptr;
        } else {
            ERR(wsP->PF_GetPixelFormat(inW, &fmt));
        }
        const int dx = prd->inRect.left - prd->outRect.left, dy = prd->inRect.top - prd->outRect.top;
        if (!err && BytesPerPixel(fmt) == 0) err = PF_Err_BAD_CALLBACK_PARAM;
        // pass-through first: input pixels outside the layer rect (grown by an effect above) survive as they are
        if (!err) err = CopyWorld(inW, outW, fmt, dx, dy);

        std::string problem;
        std::shared_ptr<const rs::Source> src;
        if (!err && shaderId > 0) {
            std::string path = rs::resolveShader(shaderId);
            if (path.empty()) problem = "id " + std::to_string(shaderId) + " not in " + rs::registryPath();
            else { src = rs::loadSource(path); if (!src->error.empty()) { problem = src->error; src.reset(); } }
        }
        rs::Gpu* G = (!err && src) ? gpu() : nullptr;
        if (!err && src && !G) problem = "GPU unavailable: " + g_gpuError;

        const bool hasLayer = prd->layerRect.right > prd->layerRect.left && prd->layerRect.bottom > prd->layerRect.top;
        if (!err && src && G && hasLayer) {
            // the shader's frame = the layer rect (uv 0..1, `size`, points in layer px), not the buffer
            const int w = (int)(prd->layerRect.right - prd->layerRect.left), h = (int)(prd->layerRect.bottom - prd->layerRect.top);
            const rs::PixelFmt pf = fmt == PF_PixelFormat_ARGB32 ? rs::PixelFmt::RGBA8 : rs::PixelFmt::RGBA32F;
            std::vector<uint8_t> texels, result((size_t)w * h * (pf == rs::PixelFmt::RGBA8 ? 4 : 16));
            WorldToTexels(inW, fmt, w, h, prd->inRect.left - prd->layerRect.left, prd->inRect.top - prd->layerRect.top, pf, texels);
            // uniforms: host fields + generic sliders
            const rs::ShaderInfo& info = src->info;
            std::vector<uint8_t> ubo(info.uboSize, 0);
            const double seconds = in_data->time_scale ? (double)in_data->current_time / in_data->time_scale : 0.0;
            const double frame = in_data->time_step ? (double)in_data->current_time / in_data->time_step : 0.0;
            const double stepFps = pv[RS_PARAM_STEP_FPS], fxFps = pv[RS_PARAM_FX_FPS];
            const double tick = stepFps > 0 ? std::floor(seconds * stepFps + 1e-4) : std::floor(frame + 1e-4);
            const double fxTick = fxFps > 0 ? std::floor(seconds * fxFps + 1e-4) : tick;
            ParamMap m = mapParams(info);
            for (size_t i = 0; i < info.params.size(); ++i) {
                const auto& f = info.params[i];
                double v[4] = {0, 0, 0, 0};
                if (f.name == "size") { v[0] = w; v[1] = h; }
                else if (f.name == "tick") v[0] = tick;
                else if (f.name == "fxTick") v[0] = fxTick;
                else if (f.name == "seed") v[0] = pv[RS_PARAM_SEED];
                else if (m.slot[i] == Slot::Slider) for (int c = 0; c < f.count; ++c) v[c] = pv[RS_PARAM_P1 + m.index[i] + c];
                else if (m.slot[i] == Slot::Color) for (int c = 0; c < 4; ++c) v[c] = colors[m.index[i]][c];
                else if (m.slot[i] == Slot::Point) { v[0] = points[m.index[i]][0]; v[1] = points[m.index[i]][1]; }
                rs::writeField(ubo, f, v, f.count);
            }
            rs::RenderRequest rq;
            // extra textures (Layer params): converted like the source, sampled by the shader through uv 0..1
            std::vector<std::vector<uint8_t>> texBufs(m.textureBinding.size());
            for (size_t i = 0; i < m.textureBinding.size(); ++i) {
                if (!prd->texChecked[i]) continue;
                PF_Err terr = extra->cb->checkout_layer_pixels(in_data->effect_ref, RS_CHECKOUT_TEX1 + (int)i, &texW[i]);
                if (terr || !texW[i] || !texW[i]->data) { texW[i] = nullptr; continue; }   // layer param set to None
                PF_PixelFormat tf = fmt;
                if (wsP) wsP->PF_GetPixelFormat(texW[i], &tf);
                WorldToTexels(texW[i], tf, texW[i]->width, texW[i]->height, 0, 0, pf, texBufs[i]);
                rs::ImageRef img;
                img.width = texW[i]->width; img.height = texW[i]->height; img.fmt = pf; img.data = texBufs[i].data();
                img.rowBytes = (size_t)img.width * (pf == rs::PixelFmt::RGBA8 ? 4 : 16);
                rq.textures.push_back({m.textureName[i], img});
            }
            rq.key = src->key;
            rq.source = &src->text;
            rq.width = w; rq.height = h; rq.fmt = pf;
            rq.src.width = w; rq.src.height = h; rq.src.fmt = pf; rq.src.data = texels.data(); rq.src.rowBytes = (size_t)w * (pf == rs::PixelFmt::RGBA8 ? 4 : 16);
            rq.ubo = ubo;
            rq.out = result.data();
            std::string rerr;
            if (G->render(rq, rerr)) {
                TexelsToWorld(result, w, h, prd->layerRect.left - prd->outRect.left, prd->layerRect.top - prd->outRect.top, pf, outW, fmt);
                setStatus(shaderId, "");
            } else {
                problem = rerr;
            }
            std::string lg = G->takeLog();
            if (!lg.empty()) rs::logLine(lg);
        }
        if (!err && !problem.empty()) setStatus(shaderId, problem);   // the pass-through above stays (never a dialog)
        if (wsP) basic->ReleaseSuite(kPFWorldSuite, kPFWorldSuiteVersion2);
    }
    for (int i = 0; i < RS_NUM_TEXTURES; ++i) if (texW[i]) ERR2(extra->cb->checkin_layer_pixels(in_data->effect_ref, RS_CHECKOUT_TEX1 + i));
    if (inW) ERR2(extra->cb->checkin_layer_pixels(in_data->effect_ref, RS_CHECKOUT_INPUT));
    return err;
}

// ---------------------------------------------------------------------------------------------- entry
extern "C" DllExport PF_Err PluginDataEntryFunction2(PF_PluginDataPtr inPtr, PF_PluginDataCB2 inPluginDataCallBackPtr,
                                                     SPBasicSuite* inSPBasicSuitePtr, const char* inHostName,
                                                     const char* inHostVersion) {
    PF_Err result = PF_Err_INVALID_CALLBACK;
    result = PF_REGISTER_EFFECT_EXT2(inPtr, inPluginDataCallBackPtr, RS_NAME, RS_MATCH_NAME, RS_CATEGORY,
                                     AE_RESERVED_INFO, "EffectMain", RS_SUPPORT_URL);
    return result;
}

extern "C" DllExport PF_Err EffectMain(PF_Cmd cmd, PF_InData* in_data, PF_OutData* out_data, PF_ParamDef* params[],
                                       PF_LayerDef* output, void* extra) {
    PF_Err err = PF_Err_NONE;
    try {
        switch (cmd) {
            case PF_Cmd_ABOUT: err = About(in_data, out_data); break;
            case PF_Cmd_GLOBAL_SETUP: err = GlobalSetup(in_data, out_data); break;
            case PF_Cmd_GLOBAL_SETDOWN: err = GlobalSetdown(in_data, out_data); break;
            case PF_Cmd_PARAMS_SETUP: err = ParamsSetup(in_data, out_data); break;
            case PF_Cmd_UPDATE_PARAMS_UI: err = UpdateParamsUI(in_data, out_data, params); break;
            case PF_Cmd_USER_CHANGED_PARAM: err = UserChangedParam(in_data, out_data, params, reinterpret_cast<const PF_UserChangedParamExtra*>(extra)); break;
            case PF_Cmd_SMART_PRE_RENDER: err = PreRender(in_data, out_data, reinterpret_cast<PF_PreRenderExtra*>(extra)); break;
            case PF_Cmd_SMART_RENDER: err = SmartRender(in_data, out_data, reinterpret_cast<PF_SmartRenderExtra*>(extra)); break;
            default: break;
        }
    } catch (...) {
        err = PF_Err_INTERNAL_STRUCT_DAMAGED;
        rs::logLine("C++ exception caught in EffectMain, cmd " + std::to_string((int)cmd));
    }
    return err;
}
