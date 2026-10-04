#include "RsGpu.h"
#include <webgpu/webgpu.h>
#include <webgpu/wgpu.h>
#include <cstdlib>
#include <cstring>
#include <cstdio>

namespace rs {

namespace {

WGPUStringView sv(const std::string& s) { return WGPUStringView{s.data(), s.size()}; }
WGPUStringView sv(const char* s) { return WGPUStringView{s, WGPU_STRLEN}; }
std::string str(WGPUStringView v) { return v.data ? (v.length == WGPU_STRLEN ? std::string(v.data) : std::string(v.data, v.length)) : std::string(); }

std::mutex g_logMutex;
std::string g_log;
void appendLog(const std::string& s) {
    std::lock_guard<std::mutex> lk(g_logMutex);
    if (g_log.size() < 64 * 1024) g_log += s + "\n";
}
void logCallback(WGPULogLevel level, WGPUStringView message, void*) {
    if (level <= WGPULogLevel_Warn) appendLog(std::string(level == WGPULogLevel_Error ? "wgpu error: " : "wgpu warn: ") + str(message));
}
void uncapturedError(WGPUDevice const*, WGPUErrorType type, WGPUStringView message, void*, void*) {
    appendLog("wgpu uncaptured error (" + std::to_string((int)type) + "): " + str(message));
}
// The platform's native backend: Metal on macOS, Direct3D 12 on Windows, Vulkan elsewhere (rs_apply on Linux).
// RS_WGPU_BACKEND=metal|dx12|vulkan|gl|any overrides it; RS_WGPU_FALLBACK=1 asks for the software adapter (WARP on
// Windows), which is what a GPU-less CI machine has.
WGPUBackendType nativeBackend() {
#if defined(__APPLE__)
    WGPUBackendType b = WGPUBackendType_Metal;
#elif defined(_WIN32)
    WGPUBackendType b = WGPUBackendType_D3D12;
#else
    WGPUBackendType b = WGPUBackendType_Vulkan;
#endif
    if (const char* e = std::getenv("RS_WGPU_BACKEND")) {
        std::string v(e);
        if (v == "metal") b = WGPUBackendType_Metal;
        else if (v == "dx12" || v == "d3d12") b = WGPUBackendType_D3D12;
        else if (v == "vulkan") b = WGPUBackendType_Vulkan;
        else if (v == "gl") b = WGPUBackendType_OpenGL;
        else if (v == "any") b = WGPUBackendType_Undefined;
    }
    return b;
}
const char* backendName(WGPUBackendType b) {
    switch (b) {
        case WGPUBackendType_Metal: return "Metal";
        case WGPUBackendType_D3D12: return "Direct3D 12";
        case WGPUBackendType_Vulkan: return "Vulkan";
        case WGPUBackendType_OpenGL: return "OpenGL";
        default: return "any";
    }
}
void deviceLost(WGPUDevice const*, WGPUDeviceLostReason reason, WGPUStringView message, void*, void*) {
    appendLog("wgpu device lost (" + std::to_string((int)reason) + "): " + str(message));
}

WGPUTextureFormat toWgpu(PixelFmt f) { return f == PixelFmt::RGBA8 ? WGPUTextureFormat_RGBA8Unorm : WGPUTextureFormat_RGBA32Float; }
size_t texelSize(PixelFmt f) { return f == PixelFmt::RGBA8 ? 4 : 16; }

}  // namespace

struct Gpu::Impl {
    WGPUInstance instance = nullptr;
    WGPUAdapter adapter = nullptr;
    WGPUDevice device = nullptr;
    WGPUQueue queue = nullptr;
    WGPUSampler sampler = nullptr;

    // wgpu-native does not implement wgpuInstanceWaitAny (it panics): its callbacks fire either synchronously
    // (adapter, device, error scopes) or from wgpuInstanceProcessEvents / wgpuDevicePoll (buffer mapping).
    // Callbacks are created in AllowProcessEvents mode and we pump until the flag flips.
    bool wait(WGPUFuture, const bool& done, std::string& err) {
        for (int i = 0; i < 100000 && !done; ++i) {
            wgpuInstanceProcessEvents(instance);
            if (done) break;
            if (device) wgpuDevicePoll(device, true, nullptr);
        }
        if (!done) { err = "wgpu callback never fired"; return false; }
        return true;
    }
    // Pop the validation error scope pushed before a risky call; returns false + message on error.
    bool popScope(std::string& err) {
        struct Out { std::string msg; bool bad = false; bool done = false; } out;
        WGPUPopErrorScopeCallbackInfo cb{};
        cb.mode = WGPUCallbackMode_AllowProcessEvents;
        cb.userdata1 = &out;
        cb.callback = [](WGPUPopErrorScopeStatus, WGPUErrorType type, WGPUStringView message, void* u1, void*) {
            Out* o = (Out*)u1;
            o->done = true;
            if (type != WGPUErrorType_NoError) { o->bad = true; o->msg = str(message); }
        };
        std::string werr;
        if (!wait(wgpuDevicePopErrorScope(device, cb), out.done, werr)) { err = werr; return false; }
        if (out.bad) { err = out.msg; return false; }
        return true;
    }
};

struct Gpu::Pipeline {
    ShaderInfo info;
    WGPUShaderModule module = nullptr;
    WGPUBindGroupLayout bgl = nullptr;
    WGPUPipelineLayout layout = nullptr;
    WGPURenderPipeline pipeline = nullptr;
    PixelFmt fmt = PixelFmt::RGBA8;   // pipelines are per output format; recompiled when it changes
    std::string source;
    std::string error;                // compile error, cached so a broken shader is not recompiled every frame
    void release() {
        if (pipeline) wgpuRenderPipelineRelease(pipeline);
        if (layout) wgpuPipelineLayoutRelease(layout);
        if (bgl) wgpuBindGroupLayoutRelease(bgl);
        if (module) wgpuShaderModuleRelease(module);
        pipeline = nullptr; layout = nullptr; bgl = nullptr; module = nullptr;
    }
    ~Pipeline() { release(); }
};

std::unique_ptr<Gpu> Gpu::create(std::string& err) {
    std::unique_ptr<Gpu> g(new Gpu());
    g->impl_.reset(new Impl());
    Impl& I = *g->impl_;
    wgpuSetLogCallback(logCallback, nullptr);
    wgpuSetLogLevel(WGPULogLevel_Warn);

    I.instance = wgpuCreateInstance(nullptr);
    if (!I.instance) { err = "wgpuCreateInstance failed"; return nullptr; }

    struct AOut { WGPUAdapter adapter = nullptr; std::string msg; bool done = false; };
    auto request = [&](WGPUBackendType backend, AOut& aout) -> bool {
        WGPURequestAdapterOptions opts{};
        opts.featureLevel = WGPUFeatureLevel_Core;
        opts.powerPreference = WGPUPowerPreference_HighPerformance;
        opts.backendType = backend;
        const char* fb = std::getenv("RS_WGPU_FALLBACK");
        opts.forceFallbackAdapter = (fb && fb[0] == '1') ? 1 : 0;
        WGPURequestAdapterCallbackInfo acb{};
        acb.mode = WGPUCallbackMode_AllowProcessEvents;
        acb.userdata1 = &aout;
        acb.callback = [](WGPURequestAdapterStatus status, WGPUAdapter adapter, WGPUStringView message, void* u1, void*) {
            AOut* o = (AOut*)u1;
            o->done = true;
            if (status == WGPURequestAdapterStatus_Success) o->adapter = adapter; else o->msg = str(message);
        };
        return I.wait(wgpuInstanceRequestAdapter(I.instance, &opts, acb), aout.done, err);
    };
    const WGPUBackendType backend = nativeBackend();
    AOut aout;
    if (!request(backend, aout)) return nullptr;
    if (!aout.adapter && backend != WGPUBackendType_Undefined) {
        // e.g. a Windows machine without Direct3D 12: let wgpu pick any backend it has (Vulkan, OpenGL)
        AOut any;
        if (!request(WGPUBackendType_Undefined, any)) return nullptr;
        if (any.adapter) aout = any;
    }
    if (!aout.adapter) { err = std::string("no ") + backendName(backend) + " adapter: " + aout.msg; return nullptr; }
    I.adapter = aout.adapter;
    WGPUAdapterInfo ainfo{};
    if (wgpuAdapterGetInfo(I.adapter, &ainfo) == WGPUStatus_Success) { g->adapterName_ = str(ainfo.device); wgpuAdapterInfoFreeMembers(ainfo); }
    g->float32Filterable_ = wgpuAdapterHasFeature(I.adapter, WGPUFeatureName_Float32Filterable);

    WGPUFeatureName req[] = {WGPUFeatureName_Float32Filterable};
    WGPUDeviceDescriptor ddesc{};
    ddesc.label = sv("RiveShader");
    ddesc.requiredFeatureCount = g->float32Filterable_ ? 1 : 0;
    ddesc.requiredFeatures = req;
    ddesc.deviceLostCallbackInfo.mode = WGPUCallbackMode_AllowSpontaneous;
    ddesc.deviceLostCallbackInfo.callback = deviceLost;
    ddesc.uncapturedErrorCallbackInfo.callback = uncapturedError;
    struct DOut { WGPUDevice device = nullptr; std::string msg; bool done = false; } dout;
    WGPURequestDeviceCallbackInfo dcb{};
    dcb.mode = WGPUCallbackMode_AllowProcessEvents;
    dcb.userdata1 = &dout;
    dcb.callback = [](WGPURequestDeviceStatus status, WGPUDevice device, WGPUStringView message, void* u1, void*) {
        DOut* o = (DOut*)u1;
        o->done = true;
        if (status == WGPURequestDeviceStatus_Success) o->device = device; else o->msg = str(message);
    };
    if (!I.wait(wgpuAdapterRequestDevice(I.adapter, &ddesc, dcb), dout.done, err)) return nullptr;
    if (!dout.device) { err = "no device: " + dout.msg; return nullptr; }
    I.device = dout.device;
    I.queue = wgpuDeviceGetQueue(I.device);

    WGPUSamplerDescriptor sd{};
    sd.label = sv("RiveShader linear clamp");
    sd.addressModeU = sd.addressModeV = sd.addressModeW = WGPUAddressMode_ClampToEdge;
    sd.magFilter = sd.minFilter = WGPUFilterMode_Linear;
    sd.mipmapFilter = WGPUMipmapFilterMode_Linear;
    sd.lodMinClamp = 0; sd.lodMaxClamp = 32;
    sd.compare = WGPUCompareFunction_Undefined;
    sd.maxAnisotropy = 1;
    I.sampler = wgpuDeviceCreateSampler(I.device, &sd);
    return g;
}

Gpu::~Gpu() {
    std::lock_guard<std::mutex> lk(mutex_);
    cache_.clear();
    if (!impl_) return;
    Impl& I = *impl_;
    if (I.sampler) wgpuSamplerRelease(I.sampler);
    if (I.queue) wgpuQueueRelease(I.queue);
    if (I.device) wgpuDeviceRelease(I.device);
    if (I.adapter) wgpuAdapterRelease(I.adapter);
    if (I.instance) wgpuInstanceRelease(I.instance);
}

std::string Gpu::takeLog() {
    std::lock_guard<std::mutex> lk(g_logMutex);
    std::string s; s.swap(g_log); return s;
}

void Gpu::forget(const std::string& key) {
    std::lock_guard<std::mutex> lk(mutex_);
    cache_.erase(key);
}

// Build (or reuse) the pipeline for the given key. Called with mutex_ held by render(), or directly (locks).
const ShaderInfo* Gpu::compile(const std::string& key, const std::string& source, std::string& err) {
    std::lock_guard<std::mutex> lk(mutex_);
    auto it = cache_.find(key);
    if (it != cache_.end()) {
        if (!it->second->error.empty()) { err = it->second->error; return nullptr; }
        return &it->second->info;
    }
    err.clear();
    return nullptr;  // not compiled yet: render() compiles for the requested format
}

bool Gpu::render(const RenderRequest& rq, std::string& err) {
    std::lock_guard<std::mutex> lk(mutex_);
    Impl& I = *impl_;
    if (rq.width <= 0 || rq.height <= 0 || !rq.out) { err = "bad render request"; return false; }
    if (rq.fmt == PixelFmt::RGBA32F && !float32Filterable_) { err = "rgba32float textures are not filterable on this GPU"; return false; }

    // ---- pipeline (cached per key + format)
    Pipeline* P = nullptr;
    auto it = cache_.find(rq.key);
    if (it != cache_.end() && it->second->fmt == rq.fmt) P = it->second.get();
    if (!P) {
        if (!rq.source) { err = "shader source missing for " + rq.key; return false; }
        std::unique_ptr<Pipeline> np(new Pipeline());
        np->fmt = rq.fmt;
        np->source = *rq.source;
        np->info = parseShader(np->source);
        if (!np->info.error.empty()) {
            np->error = np->info.error;
        } else {
            wgpuDevicePushErrorScope(I.device, WGPUErrorFilter_Validation);
            WGPUShaderSourceWGSL wgsl{};
            wgsl.chain.sType = WGPUSType_ShaderSourceWGSL;
            wgsl.code = sv(np->source);
            WGPUShaderModuleDescriptor smd{};
            smd.nextInChain = &wgsl.chain;
            smd.label = sv(rq.key);
            np->module = wgpuDeviceCreateShaderModule(I.device, &smd);
            std::string serr;
            if (!I.popScope(serr)) np->error = "shader compile: " + serr;
        }
        if (np->error.empty()) {
            std::vector<WGPUBindGroupLayoutEntry> entries;
            for (const Binding& b : np->info.bindings) {
                WGPUBindGroupLayoutEntry e{};
                e.binding = (uint32_t)b.binding;
                e.visibility = WGPUShaderStage_Fragment | WGPUShaderStage_Vertex;
                if (b.kind == BindKind::Texture) { e.texture.sampleType = WGPUTextureSampleType_Float; e.texture.viewDimension = WGPUTextureViewDimension_2D; }
                else if (b.kind == BindKind::Sampler) { e.sampler.type = WGPUSamplerBindingType_Filtering; }
                else if (b.kind == BindKind::Uniform) { e.buffer.type = WGPUBufferBindingType_Uniform; e.buffer.minBindingSize = 0; }
                entries.push_back(e);
            }
            wgpuDevicePushErrorScope(I.device, WGPUErrorFilter_Validation);
            WGPUBindGroupLayoutDescriptor bgld{};
            bgld.label = sv("RiveShader group 0");
            bgld.entryCount = entries.size();
            bgld.entries = entries.data();
            np->bgl = wgpuDeviceCreateBindGroupLayout(I.device, &bgld);
            WGPUPipelineLayoutDescriptor pld{};
            pld.bindGroupLayoutCount = 1;
            pld.bindGroupLayouts = &np->bgl;
            np->layout = wgpuDeviceCreatePipelineLayout(I.device, &pld);
            WGPUColorTargetState target{};
            target.format = toWgpu(rq.fmt);
            target.writeMask = WGPUColorWriteMask_All;
            WGPUFragmentState fs{};
            fs.module = np->module;
            fs.entryPoint = sv(np->info.fs);
            fs.targetCount = 1;
            fs.targets = &target;
            WGPURenderPipelineDescriptor rpd{};
            rpd.label = sv(rq.key);
            rpd.layout = np->layout;
            rpd.vertex.module = np->module;
            rpd.vertex.entryPoint = sv(np->info.vs);
            rpd.primitive.topology = WGPUPrimitiveTopology_TriangleList;
            rpd.primitive.stripIndexFormat = WGPUIndexFormat_Undefined;
            rpd.primitive.frontFace = WGPUFrontFace_CCW;
            rpd.primitive.cullMode = WGPUCullMode_None;
            rpd.multisample.count = 1;
            rpd.multisample.mask = 0xFFFFFFFF;
            rpd.fragment = &fs;
            np->pipeline = wgpuDeviceCreateRenderPipeline(I.device, &rpd);
            std::string perr;
            if (!I.popScope(perr)) np->error = "pipeline: " + perr;
        }
        if (!np->error.empty()) np->release();
        P = np.get();
        cache_[rq.key] = std::move(np);
    }
    if (!P->error.empty()) { err = P->error; return false; }

    // ---- per-render resources
    const size_t tsz = texelSize(rq.fmt);
    std::vector<WGPUTexture> textures;
    std::vector<WGPUTextureView> views;
    auto upload = [&](const ImageRef& img) -> WGPUTextureView {
        int w = img.data ? img.width : 1, h = img.data ? img.height : 1;
        WGPUTextureDescriptor td{};
        td.usage = WGPUTextureUsage_TextureBinding | WGPUTextureUsage_CopyDst;
        td.dimension = WGPUTextureDimension_2D;
        td.size = {(uint32_t)w, (uint32_t)h, 1};
        td.format = toWgpu(img.data ? img.fmt : rq.fmt);
        td.mipLevelCount = 1;
        td.sampleCount = 1;
        WGPUTexture t = wgpuDeviceCreateTexture(I.device, &td);
        textures.push_back(t);
        WGPUTexelCopyTextureInfo dst{};
        dst.texture = t;
        dst.aspect = WGPUTextureAspect_All;
        WGPUTexelCopyBufferLayout layout{};
        WGPUExtent3D ext{(uint32_t)w, (uint32_t)h, 1};
        if (img.data) {
            size_t ts = texelSize(img.fmt);
            layout.bytesPerRow = (uint32_t)(img.rowBytes ? img.rowBytes : w * ts);
            layout.rowsPerImage = (uint32_t)h;
            wgpuQueueWriteTexture(I.queue, &dst, img.data, layout.bytesPerRow * (size_t)h, &layout, &ext);
        } else {
            uint8_t zero[16] = {0};
            layout.bytesPerRow = (uint32_t)tsz;
            layout.rowsPerImage = 1;
            wgpuQueueWriteTexture(I.queue, &dst, zero, tsz, &layout, &ext);
        }
        WGPUTextureView v = wgpuTextureCreateView(t, nullptr);
        views.push_back(v);
        return v;
    };

    std::vector<uint8_t> ubo = rq.ubo;
    if (ubo.size() < (size_t)P->info.uboSize) ubo.resize(P->info.uboSize, 0);
    if (ubo.empty()) ubo.resize(16, 0);
    WGPUBufferDescriptor ubd{};
    ubd.label = sv("Params");
    ubd.usage = WGPUBufferUsage_Uniform | WGPUBufferUsage_CopyDst;
    ubd.size = ubo.size();
    WGPUBuffer uboBuf = wgpuDeviceCreateBuffer(I.device, &ubd);
    wgpuQueueWriteBuffer(I.queue, uboBuf, 0, ubo.data(), ubo.size());

    std::vector<WGPUBindGroupEntry> bge;
    bool firstTex = true;
    for (const Binding& b : P->info.bindings) {
        WGPUBindGroupEntry e{};
        e.binding = (uint32_t)b.binding;
        if (b.kind == BindKind::Texture) {
            if (firstTex) { e.textureView = upload(rq.src); firstTex = false; }
            else {
                const ImageRef* found = nullptr;
                for (const auto& kv : rq.textures) if (kv.first == b.name) found = &kv.second;
                e.textureView = upload(found ? *found : ImageRef{});
            }
        } else if (b.kind == BindKind::Sampler) {
            e.sampler = I.sampler;
        } else if (b.kind == BindKind::Uniform) {
            e.buffer = uboBuf; e.offset = 0; e.size = ubo.size();
        }
        bge.push_back(e);
    }
    WGPUBindGroupDescriptor bgd{};
    bgd.layout = P->bgl;
    bgd.entryCount = bge.size();
    bgd.entries = bge.data();
    wgpuDevicePushErrorScope(I.device, WGPUErrorFilter_Validation);
    WGPUBindGroup bg = wgpuDeviceCreateBindGroup(I.device, &bgd);

    WGPUTextureDescriptor otd{};
    otd.usage = WGPUTextureUsage_RenderAttachment | WGPUTextureUsage_CopySrc;
    otd.dimension = WGPUTextureDimension_2D;
    otd.size = {(uint32_t)rq.width, (uint32_t)rq.height, 1};
    otd.format = toWgpu(rq.fmt);
    otd.mipLevelCount = 1;
    otd.sampleCount = 1;
    WGPUTexture outTex = wgpuDeviceCreateTexture(I.device, &otd);
    WGPUTextureView outView = wgpuTextureCreateView(outTex, nullptr);

    const size_t bpr = ((size_t)rq.width * tsz + 255) / 256 * 256;
    WGPUBufferDescriptor rbd{};
    rbd.label = sv("readback");
    rbd.usage = WGPUBufferUsage_MapRead | WGPUBufferUsage_CopyDst;
    rbd.size = bpr * (size_t)rq.height;
    WGPUBuffer readback = wgpuDeviceCreateBuffer(I.device, &rbd);

    WGPUCommandEncoder enc = wgpuDeviceCreateCommandEncoder(I.device, nullptr);
    WGPURenderPassColorAttachment ca{};
    ca.view = outView;
    ca.depthSlice = WGPU_DEPTH_SLICE_UNDEFINED;
    ca.loadOp = WGPULoadOp_Clear;
    ca.storeOp = WGPUStoreOp_Store;
    ca.clearValue = {0, 0, 0, 0};
    WGPURenderPassDescriptor rpd{};
    rpd.colorAttachmentCount = 1;
    rpd.colorAttachments = &ca;
    WGPURenderPassEncoder pass = wgpuCommandEncoderBeginRenderPass(enc, &rpd);
    wgpuRenderPassEncoderSetPipeline(pass, P->pipeline);
    wgpuRenderPassEncoderSetBindGroup(pass, 0, bg, 0, nullptr);
    wgpuRenderPassEncoderDraw(pass, 3, 1, 0, 0);
    wgpuRenderPassEncoderEnd(pass);
    wgpuRenderPassEncoderRelease(pass);
    WGPUTexelCopyTextureInfo csrc{};
    csrc.texture = outTex;
    csrc.aspect = WGPUTextureAspect_All;
    WGPUTexelCopyBufferInfo cdst{};
    cdst.buffer = readback;
    cdst.layout.bytesPerRow = (uint32_t)bpr;
    cdst.layout.rowsPerImage = (uint32_t)rq.height;
    WGPUExtent3D ext{(uint32_t)rq.width, (uint32_t)rq.height, 1};
    wgpuCommandEncoderCopyTextureToBuffer(enc, &csrc, &cdst, &ext);
    WGPUCommandBuffer cmd = wgpuCommandEncoderFinish(enc, nullptr);
    wgpuCommandEncoderRelease(enc);
    wgpuQueueSubmit(I.queue, 1, &cmd);
    wgpuCommandBufferRelease(cmd);

    bool ok = true;
    std::string verr;
    if (!I.popScope(verr)) { err = "render: " + verr; ok = false; }
    if (ok) {
        struct MOut { bool done = false; bool good = false; std::string msg; } mout;
        WGPUBufferMapCallbackInfo mcb{};
        mcb.mode = WGPUCallbackMode_AllowProcessEvents;
        mcb.userdata1 = &mout;
        mcb.callback = [](WGPUMapAsyncStatus status, WGPUStringView message, void* u1, void*) {
            MOut* o = (MOut*)u1; o->done = true; o->good = status == WGPUMapAsyncStatus_Success; o->msg = str(message);
        };
        WGPUFuture mf = wgpuBufferMapAsync(readback, WGPUMapMode_Read, 0, rbd.size, mcb);
        if (!I.wait(mf, mout.done, verr) || !mout.good) { err = "readback: " + (verr.empty() ? mout.msg : verr); ok = false; }
        else {
            const uint8_t* mapped = (const uint8_t*)wgpuBufferGetConstMappedRange(readback, 0, rbd.size);
            if (!mapped) { err = "readback: null mapped range"; ok = false; }
            else {
                const size_t orb = rq.outRowBytes ? rq.outRowBytes : (size_t)rq.width * tsz;
                for (int y = 0; y < rq.height; ++y)
                    std::memcpy((uint8_t*)rq.out + (size_t)y * orb, mapped + (size_t)y * bpr, (size_t)rq.width * tsz);
            }
            wgpuBufferUnmap(readback);
        }
    }

    wgpuBufferRelease(readback);
    wgpuTextureViewRelease(outView);
    wgpuTextureRelease(outTex);
    if (bg) wgpuBindGroupRelease(bg);
    wgpuBufferRelease(uboBuf);
    for (auto v : views) wgpuTextureViewRelease(v);
    for (auto t : textures) wgpuTextureRelease(t);
    return ok;
}

}  // namespace rs
