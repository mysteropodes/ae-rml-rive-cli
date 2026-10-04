#include "RsRegistry.h"
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <map>
#include <mutex>
#include <sstream>
#include <sys/stat.h>
#ifdef _WIN32
#include <direct.h>
#include <windows.h>
#endif

namespace rs {

namespace {
std::mutex g_mutex;
std::map<int, std::string> g_registry;
time_t g_registryMtime = -1;
std::map<std::string, std::shared_ptr<const Source>> g_sources;

// Paths are UTF-8 everywhere in the plugin; Windows needs them as UTF-16 for the file APIs.
#ifdef _WIN32
std::wstring wide(const std::string& s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    if (n > 0) MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}
std::string utf8(const wchar_t* w) {
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
    std::string s(n > 0 ? n - 1 : 0, '\0');
    if (n > 1) WideCharToMultiByte(CP_UTF8, 0, w, -1, &s[0], n, nullptr, nullptr);
    return s;
}
#endif

time_t mtimeOf(const std::string& p) {
#ifdef _WIN32
    struct _stat64 st;
    return _wstat64(wide(p).c_str(), &st) == 0 ? (time_t)st.st_mtime : 0;
#else
    struct stat st;
    return stat(p.c_str(), &st) == 0 ? st.st_mtime : 0;
#endif
}

FILE* openFile(const std::string& p, const char* mode) {
#ifdef _WIN32
    return _wfopen(wide(p).c_str(), wide(mode).c_str());
#else
    return fopen(p.c_str(), mode);
#endif
}

bool readFile(const std::string& p, std::string& out) {
    FILE* f = openFile(p, "rb");
    if (!f) return false;
    char buf[65536];
    size_t n;
    out.clear();
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) out.append(buf, n);
    fclose(f);
    return true;
}

void reloadRegistryLocked() {
    const std::string p = registryPath();
    time_t mt = mtimeOf(p);
    if (mt == g_registryMtime) return;
    g_registryMtime = mt;
    g_registry.clear();
    std::string text;
    readFile(p, text);
    std::istringstream f(text);
    std::string line;
    while (std::getline(f, line)) {
        size_t tab = line.find('\t');
        if (tab == std::string::npos || line.empty() || line[0] == '#') continue;
        int id = atoi(line.substr(0, tab).c_str());
        std::string path = line.substr(tab + 1);
        while (!path.empty() && (path.back() == '\r' || path.back() == '\n')) path.pop_back();
        if (id > 0 && !path.empty()) g_registry[id] = path;
    }
}
}  // namespace

std::string supportDir() {
#ifdef _WIN32
    const wchar_t* appdata = _wgetenv(L"APPDATA");
    std::string d = (appdata ? utf8(appdata) : std::string("C:\\Temp")) + "\\RiveShader";
    _wmkdir(wide(d).c_str());
    return d;
#else
    const char* home = getenv("HOME");
    std::string d = std::string(home ? home : "/tmp") + "/Library/Application Support/RiveShader";
    mkdir(d.c_str(), 0755);
    return d;
#endif
}
#ifdef _WIN32
#define RS_SEP "\\"
#else
#define RS_SEP "/"
#endif
std::string registryPath() { return supportDir() + RS_SEP "shaders.tsv"; }
std::string logPath() { return supportDir() + RS_SEP "riveshader.log"; }

void logLine(const std::string& s) {
    FILE* f = openFile(logPath(), "a");
    if (!f) return;
    time_t t = time(nullptr);
    char buf[32];
    strftime(buf, sizeof buf, "%Y-%m-%d %H:%M:%S", localtime(&t));
    fprintf(f, "%s %s\n", buf, s.c_str());
    fclose(f);
}

std::string resolveShader(int id) {
    std::lock_guard<std::mutex> lk(g_mutex);
    reloadRegistryLocked();
    auto it = g_registry.find(id);
    return it == g_registry.end() ? std::string() : it->second;
}

int registerShader(const std::string& path) {
    std::lock_guard<std::mutex> lk(g_mutex);
    reloadRegistryLocked();
    int maxId = 0;
    for (const auto& kv : g_registry) {
        if (kv.second == path) return kv.first;
        if (kv.first > maxId) maxId = kv.first;
    }
    int id = maxId + 1;
    FILE* f = openFile(registryPath(), "a");
    if (!f) return 0;
    fprintf(f, "%d\t%s\n", id, path.c_str());
    fclose(f);
    g_registry[id] = path;
    g_registryMtime = mtimeOf(registryPath());
    return id;
}

std::shared_ptr<const Source> loadSource(const std::string& path) {
    std::lock_guard<std::mutex> lk(g_mutex);
    time_t mt = mtimeOf(path);
    std::string key = path + "@" + std::to_string((long long)mt);
    auto it = g_sources.find(path);
    if (it != g_sources.end() && it->second->key == key) return it->second;
    auto s = std::make_shared<Source>();
    s->path = path;
    s->key = key;
    if (!readFile(path, s->text)) {
        s->error = "cannot read " + path;
    } else {
        s->info = parseShader(s->text);
        s->error = s->info.error;
    }
    g_sources[path] = s;
    return s;
}

}  // namespace rs
