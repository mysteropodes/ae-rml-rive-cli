// Windows counterpart of RsDialog.mm: the common Open dialog (IFileOpenDialog), .wgsl files, UTF-8 path out.
#include "RsDialog.h"
#include <windows.h>
#include <shobjidl.h>

namespace rs {
std::string pickWgslFile() {
    std::string out;
    // AE's UI thread has COM initialised already (S_FALSE / RPC_E_CHANGED_MODE): only undo what we did ourselves
    HRESULT init = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    IFileOpenDialog* dlg = nullptr;
    if (SUCCEEDED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&dlg)))) {
        COMDLG_FILTERSPEC types[] = {{L"Rive WGSL shader (*.wgsl)", L"*.wgsl"}, {L"All files", L"*.*"}};
        dlg->SetFileTypes(2, types);
        dlg->SetTitle(L"Choose a Rive .wgsl shader");
        DWORD flags = 0;
        if (SUCCEEDED(dlg->GetOptions(&flags))) dlg->SetOptions(flags | FOS_FORCEFILESYSTEM | FOS_FILEMUSTEXIST);
        // owner = the active window (AE's main window when its Effect Controls button is clicked)
        if (SUCCEEDED(dlg->Show(GetActiveWindow()))) {
            IShellItem* item = nullptr;
            if (SUCCEEDED(dlg->GetResult(&item))) {
                PWSTR path = nullptr;
                if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path)) && path) {
                    int n = WideCharToMultiByte(CP_UTF8, 0, path, -1, nullptr, 0, nullptr, nullptr);
                    if (n > 1) {
                        out.resize(n - 1);
                        WideCharToMultiByte(CP_UTF8, 0, path, -1, &out[0], n, nullptr, nullptr);
                    }
                    CoTaskMemFree(path);
                }
                item->Release();
            }
        }
        dlg->Release();
    }
    if (init == S_OK || init == S_FALSE) CoUninitialize();
    return out;
}
}  // namespace rs
