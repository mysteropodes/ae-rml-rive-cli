// wgpu-native (Metal) backend for Rive Shader: one device for the process, a pipeline cache keyed by shader,
// and a synchronous upload -> full-screen pass -> readback. Transposed 1:1 from rml2ae/wgsl_apply.py.
#pragma once
#include "RsShader.h"
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#include <cstdint>

namespace rs {

enum class PixelFmt { RGBA8, RGBA32F };   // texel formats used on the GPU side (rgba8unorm / rgba32float)

struct ImageRef {
    int width = 0, height = 0;
    PixelFmt fmt = PixelFmt::RGBA8;
    const void* data = nullptr;   // rows of width texels, `rowBytes` apart; nullptr = 1x1 transparent
    size_t rowBytes = 0;
};

struct RenderRequest {
    std::string key;                       // pipeline cache key (shader path + mtime, or any stable id)
    const std::string* source = nullptr;   // WGSL text, only read when `key` is not cached yet
    int width = 0, height = 0;             // output size
    PixelFmt fmt = PixelFmt::RGBA8;        // texel format for source, extra textures and output
    ImageRef src;                          // binding 0 (rows packed or strided)
    std::vector<std::pair<std::string, ImageRef>> textures;   // extra textures by binding name
    std::vector<uint8_t> ubo;              // packed `struct Params` bytes (see RsShader)
    void* out = nullptr;                   // width x height texels in `fmt`
    size_t outRowBytes = 0;
};

class Gpu {
public:
    // nullptr + message when no Metal adapter/device is available.
    static std::unique_ptr<Gpu> create(std::string& err);
    ~Gpu();

    // Compile (or fetch from the cache) the pipeline for `key`; returns the parsed info or nullptr + err.
    // The cache keeps at most one entry per key; a new key with the same shader path evicts the old one.
    const ShaderInfo* compile(const std::string& key, const std::string& source, std::string& err);
    void forget(const std::string& key);

    bool render(const RenderRequest& rq, std::string& err);

    bool float32Filterable() const { return float32Filterable_; }
    const std::string& adapterName() const { return adapterName_; }
    // Messages from wgpu's logger / uncaptured errors since the last call (for the plugin log).
    std::string takeLog();

private:
    Gpu() = default;
    struct Impl;
    struct Pipeline;
    std::unique_ptr<Impl> impl_;
    std::map<std::string, std::unique_ptr<Pipeline>> cache_;
    std::mutex mutex_;   // one render at a time (no THREADED_RENDERING flag yet, belt and braces)
    bool float32Filterable_ = false;
    std::string adapterName_;
};

}  // namespace rs
