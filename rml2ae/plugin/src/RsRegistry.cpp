#include "RsRegistry.h"
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <map>
#include <mutex>
#include <sstream>
#include <sys/stat.h>

namespace rs {

namespace {
std::mutex g_mutex;
std::map<int, std::string> g_registry;
time_t g_registryMtime = -1;
std::map<std::string, std::shared_ptr<const Source>> g_sources;

time_t mtimeOf(const std::string& p) {
    struct stat st;
    return stat(p.c_str(), &st) == 0 ? st.st_mtime : 0;
}

void reloadRegistryLocked() {
    const std::string p = registryPath();
    time_t mt = mtimeOf(p);
    if (mt == g_registryMtime) return;
    g_registryMtime = mt;
    g_registry.clear();
    std::ifstream f(p);
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
    const char* home = getenv("HOME");
    std::string d = std::string(home ? home : "/tmp") + "/Library/Application Support/RiveShader";
    mkdir(d.c_str(), 0755);
    return d;
}
std::string registryPath() { return supportDir() + "/shaders.tsv"; }
std::string logPath() { return supportDir() + "/riveshader.log"; }

void logLine(const std::string& s) {
    FILE* f = fopen(logPath().c_str(), "a");
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
    FILE* f = fopen(registryPath().c_str(), "a");
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
    std::ifstream f(path);
    if (!f) {
        s->error = "cannot read " + path;
    } else {
        std::stringstream ss;
        ss << f.rdbuf();
        s->text = ss.str();
        s->info = parseShader(s->text);
        s->error = s->info.error;
    }
    g_sources[path] = s;
    return s;
}

}  // namespace rs
