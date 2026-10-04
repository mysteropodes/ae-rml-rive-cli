// Shader registry + source cache. `~/Library/Application Support/RiveShader/shaders.tsv` maps an integer id (the
// "Shader" slider value, scriptable from ExtendScript) to a .wgsl path; rml2ae and the "Load .wgsl…" button write it.
#pragma once
#include "RsShader.h"
#include <memory>
#include <string>

namespace rs {

std::string supportDir();                 // ~/Library/Application Support/RiveShader (created on demand)
std::string registryPath();               // .../shaders.tsv
std::string logPath();                    // .../riveshader.log
void logLine(const std::string& s);       // append a timestamped line (never throws)

// id -> path ("" when unknown). Re-reads the file when its mtime changes.
std::string resolveShader(int id);
// Add (or find) a path in the registry; returns its id (0 on failure).
int registerShader(const std::string& path);

struct Source {
    std::string path;
    std::string key;      // path + '@' + mtime  (pipeline cache key)
    std::string text;
    ShaderInfo info;
    std::string error;    // read error or convention error
};
// Cached by path; refreshed when the file's mtime changes.
std::shared_ptr<const Source> loadSource(const std::string& path);

}  // namespace rs
