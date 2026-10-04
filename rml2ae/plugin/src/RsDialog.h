#pragma once
#include <string>
namespace rs {
// Modal file picker (UI thread only — PF_Cmd_USER_CHANGED_PARAM). Returns "" when cancelled.
std::string pickWgslFile();
}
