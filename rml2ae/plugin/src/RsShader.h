// Rive .wgsl conventions: `struct Params` fields -> uniform layout, @group(0) bindings, entry points.
// Mirror of rml2ae/wgsl_apply.py::parse_shader / pack_params (the oracle) — keep both in sync.
#pragma once
#include <string>
#include <vector>
#include <cstdint>

namespace rs {

struct ParamField {
    std::string name;
    std::string type;     // f32, i32, u32, vec2<f32>, vec3<f32>, vec4<f32>, vec2<i32>, vec2<u32>
    int count;            // number of scalars
    bool integer;         // i32 / u32 scalars
    int offset;           // byte offset in the uniform buffer (WGSL uniform alignment rules)
    double min, max;      // from the trailing comment "a..b" (defaults 0..1)
    double def;           // from "default x" in the comment, else 0
    bool hasRange, hasDefault;
    std::string comment;
};

enum class BindKind { Texture, Sampler, Uniform, Other };

struct Binding {
    int group, binding;
    BindKind kind;
    std::string name;
};

struct ShaderInfo {
    std::vector<ParamField> params;
    std::vector<Binding> bindings;
    std::string vs = "vs_main", fs = "fs_main";
    int uboSize = 0;      // padded to 16
    std::string error;    // non-empty when the source violates the convention
};

// Parse the WGSL text. Never throws; convention violations land in info.error.
ShaderInfo parseShader(const std::string& src);

// Reserved field names filled by the host (never exposed as effect parameters).
bool isHostField(const std::string& name);

// Write one field (scalars given as doubles) into the uniform bytes.
void writeField(std::vector<uint8_t>& ubo, const ParamField& f, const double* values, int n);

}  // namespace rs
