#include "RsShader.h"
#include <regex>
#include <cstring>
#include <cmath>

namespace rs {

namespace {
struct TypeSpec { const char* name; int count; bool integer; int size; int align; };
const TypeSpec kTypes[] = {
    {"f32", 1, false, 4, 4}, {"i32", 1, true, 4, 4}, {"u32", 1, true, 4, 4},
    {"vec2<f32>", 2, false, 8, 8}, {"vec3<f32>", 3, false, 12, 16}, {"vec4<f32>", 4, false, 16, 16},
    {"vec2<i32>", 2, true, 8, 8}, {"vec2<u32>", 2, true, 8, 8},
    {"vec2f", 2, false, 8, 8}, {"vec3f", 3, false, 12, 16}, {"vec4f", 4, false, 16, 16},
};

std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}
}  // namespace

bool isHostField(const std::string& n) {
    return n == "size" || n == "tick" || n == "fxTick" || n == "seed" || n.rfind("pad", 0) == 0 || n.rfind("_pad", 0) == 0;
}

ShaderInfo parseShader(const std::string& src) {
    ShaderInfo info;
    static const std::regex reStruct(R"(struct\s+Params\s*\{([^}]*)\})");
    static const std::regex reField(R"(^(\w+)\s*:\s*([\w<>]+))");
    static const std::regex reRange(R"((-?[\d.]+)\s*\.\.\s*(-?[\d.]+))");
    static const std::regex reDefault(R"(default\s*[=:]?\s*(-?[\d.]+))");
    static const std::regex reBind(R"(@group\((\d+)\)\s*@binding\((\d+)\)\s*var(<uniform>|<storage[^>]*>)?\s+(\w+)\s*:\s*([\w<>]+))");
    static const std::regex reVs(R"(@vertex\s*fn\s+(\w+))");
    static const std::regex reFs(R"(@fragment\s*fn\s+(\w+))");
    std::smatch m;
    int offset = 0;
    if (std::regex_search(src, m, reStruct)) {
        std::string body = m[1];
        size_t pos = 0;
        while (pos <= body.size()) {
            size_t nl = body.find('\n', pos);
            std::string line = body.substr(pos, nl == std::string::npos ? std::string::npos : nl - pos);
            pos = nl == std::string::npos ? body.size() + 1 : nl + 1;
            std::string comment;
            size_t c = line.find("//");
            if (c != std::string::npos) { comment = trim(line.substr(c + 2)); line = line.substr(0, c); }
            line = trim(line);
            while (!line.empty() && (line.back() == ',' || line.back() == ';')) line.pop_back();
            std::smatch fm;
            if (!std::regex_search(line, fm, reField)) continue;
            ParamField f;
            f.name = fm[1]; f.type = fm[2]; f.comment = comment;
            const TypeSpec* ts = nullptr;
            for (const auto& t : kTypes) if (f.type == t.name) ts = &t;
            if (!ts) { info.error = "unsupported Params field type: " + f.type + " (" + f.name + ")"; ts = &kTypes[0]; }
            f.count = ts->count; f.integer = ts->integer;
            while (offset % ts->align) ++offset;
            f.offset = offset; offset += ts->size;
            f.min = 0; f.max = 1; f.def = 0; f.hasRange = f.hasDefault = false;
            std::smatch rm;
            if (std::regex_search(comment, rm, reRange)) { f.min = std::stod(rm[1]); f.max = std::stod(rm[2]); f.hasRange = true; }
            if (std::regex_search(comment, rm, reDefault)) { f.def = std::stod(rm[1]); f.hasDefault = true; }
            info.params.push_back(f);
        }
    }
    while (offset % 16) ++offset;
    info.uboSize = offset;
    for (auto it = std::sregex_iterator(src.begin(), src.end(), reBind); it != std::sregex_iterator(); ++it) {
        const std::smatch& bm = *it;
        Binding b;
        b.group = std::stoi(bm[1]); b.binding = std::stoi(bm[2]);
        std::string space = bm[3], type = bm[5];
        b.name = bm[4];
        b.kind = space == "<uniform>" ? BindKind::Uniform : type == "sampler" ? BindKind::Sampler
               : type.rfind("texture_", 0) == 0 ? BindKind::Texture : BindKind::Other;
        if (b.group != 0 && info.error.empty()) info.error = "only @group(0) is supported (found group " + std::to_string(b.group) + " for " + b.name + ")";
        if (b.kind == BindKind::Other && info.error.empty()) info.error = "unsupported binding kind for " + b.name + " (" + type + ")";
        info.bindings.push_back(b);
    }
    if (std::regex_search(src, m, reVs)) info.vs = m[1];
    if (std::regex_search(src, m, reFs)) info.fs = m[1];
    return info;
}

void writeField(std::vector<uint8_t>& ubo, const ParamField& f, const double* values, int n) {
    if (ubo.size() < size_t(f.offset + f.count * 4)) ubo.resize(f.offset + f.count * 4, 0);
    for (int i = 0; i < f.count; ++i) {
        double v = i < n ? values[i] : (n == 1 ? values[0] : 0.0);
        uint8_t* dst = ubo.data() + f.offset + i * 4;
        if (f.integer) { int32_t iv = (int32_t)std::llround(v); std::memcpy(dst, &iv, 4); }
        else { float fv = (float)v; std::memcpy(dst, &fv, 4); }
    }
}

}  // namespace rs
