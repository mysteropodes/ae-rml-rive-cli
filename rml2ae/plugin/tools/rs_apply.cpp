// rs_apply — exercises RsGpu outside After Effects. Same semantics as rml2ae/wgsl_apply.py but on raw RGBA files:
//   rs_apply shader.wgsl in.rgba out.rgba [--fmt 8|32] [--set name=v[,v..]] [--tex name=file.rgba] [--repeat N]
// .rgba file = uint32 width, uint32 height, then width*height texels (RGBA8 or RGBA32F per --fmt).
#include "RsGpu.h"
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <chrono>

static bool readRaw(const std::string& path, int& w, int& h, std::vector<uint8_t>& data) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    uint32_t wh[2];
    f.read((char*)wh, 8);
    w = wh[0]; h = wh[1];
    data.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    return true;
}

int main(int argc, char** argv) {
    std::vector<std::string> pos;
    std::vector<std::pair<std::string, std::vector<double>>> sets;
    std::vector<std::pair<std::string, std::string>> texs;
    int fmt = 8, repeat = 1;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--fmt" && i + 1 < argc) fmt = atoi(argv[++i]);
        else if (a == "--repeat" && i + 1 < argc) repeat = atoi(argv[++i]);
        else if (a == "--set" && i + 1 < argc) {
            std::string kv = argv[++i];
            size_t eq = kv.find('=');
            std::vector<double> vals;
            std::stringstream ss(kv.substr(eq + 1));
            std::string tok;
            while (std::getline(ss, tok, ',')) vals.push_back(atof(tok.c_str()));
            sets.push_back({kv.substr(0, eq), vals});
        } else if (a == "--tex" && i + 1 < argc) {
            std::string kv = argv[++i];
            size_t eq = kv.find('=');
            texs.push_back({kv.substr(0, eq), kv.substr(eq + 1)});
        } else pos.push_back(a);
    }
    if (pos.size() < 3) { fprintf(stderr, "usage: rs_apply shader.wgsl in.rgba out.rgba [--fmt 8|32] [--set k=v] [--tex k=f]\n"); return 1; }
    std::ifstream sf(pos[0]);
    if (!sf) { fprintf(stderr, "cannot read %s\n", pos[0].c_str()); return 1; }
    std::string source((std::istreambuf_iterator<char>(sf)), std::istreambuf_iterator<char>());
    int w, h;
    std::vector<uint8_t> src;
    if (!readRaw(pos[1], w, h, src)) { fprintf(stderr, "cannot read %s\n", pos[1].c_str()); return 1; }
    rs::PixelFmt pf = fmt == 8 ? rs::PixelFmt::RGBA8 : rs::PixelFmt::RGBA32F;
    size_t tsz = fmt == 8 ? 4 : 16;

    std::string err;
    auto gpu = rs::Gpu::create(err);
    if (!gpu) { fprintf(stderr, "gpu: %s\n", err.c_str()); return 2; }
    fprintf(stderr, "adapter: %s  float32-filterable: %d\n", gpu->adapterName().c_str(), (int)gpu->float32Filterable());

    rs::ShaderInfo info = rs::parseShader(source);
    if (!info.error.empty()) { fprintf(stderr, "shader convention: %s\n", info.error.c_str()); return 3; }
    std::vector<uint8_t> ubo(info.uboSize, 0);
    for (const auto& f : info.params) {
        if (f.name == "size") { double v[2] = {(double)w, (double)h}; rs::writeField(ubo, f, v, 2); }
        for (const auto& s : sets) if (s.first == f.name) rs::writeField(ubo, f, s.second.data(), (int)s.second.size());
    }
    std::vector<std::vector<uint8_t>> texData;
    rs::RenderRequest rq;
    for (const auto& t : texs) {
        int tw, th;
        texData.emplace_back();
        if (!readRaw(t.second, tw, th, texData.back())) { fprintf(stderr, "cannot read %s\n", t.second.c_str()); return 1; }
        rs::ImageRef img; img.width = tw; img.height = th; img.fmt = pf; img.data = texData.back().data(); img.rowBytes = tw * tsz;
        rq.textures.push_back({t.first, img});
    }
    rq.key = pos[0];
    rq.source = &source;
    rq.width = w; rq.height = h; rq.fmt = pf;
    rq.src.width = w; rq.src.height = h; rq.src.fmt = pf; rq.src.data = src.data(); rq.src.rowBytes = w * tsz;
    rq.ubo = ubo;
    std::vector<uint8_t> out((size_t)w * h * tsz);
    rq.out = out.data();
    rq.outRowBytes = w * tsz;
    auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < repeat; ++i) {
        if (!gpu->render(rq, err)) { fprintf(stderr, "render: %s\n%s", err.c_str(), gpu->takeLog().c_str()); return 4; }
    }
    auto ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    fprintf(stderr, "rendered %dx%d x%d in %.2f ms (%.2f ms/frame)\n", w, h, repeat, ms, ms / repeat);
    std::string lg = gpu->takeLog();
    if (!lg.empty()) fprintf(stderr, "%s", lg.c_str());
    std::ofstream of(pos[2], std::ios::binary);
    uint32_t wh[2] = {(uint32_t)w, (uint32_t)h};
    of.write((char*)wh, 8);
    of.write((char*)out.data(), out.size());
    printf("params:");
    for (const auto& f : info.params) printf(" %s:%s@%d", f.name.c_str(), f.type.c_str(), f.offset);
    printf("\nbindings:");
    for (const auto& b : info.bindings) printf(" %d=%d %s", b.binding, (int)b.kind, b.name.c_str());
    printf("\nubo size %d\n", info.uboSize);
    return 0;
}
