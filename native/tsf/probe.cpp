#include "protocol.h"
#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void check(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
HWND bridge() {
    HWND w = nullptr;
    while ((w = FindWindowExW(HWND_MESSAGE, w, kBridgeClass, nullptr))) {
        DWORD pid = 0;
        if (GetWindowThreadProcessId(w, &pid) == GetCurrentThreadId() && pid == GetCurrentProcessId()) return w;
    }
    return nullptr;
}
LRESULT send(HWND hwnd, const std::vector<wchar_t>& payload, ULONG_PTR tag = kCopyDataTag) {
    COPYDATASTRUCT data{tag, static_cast<DWORD>(payload.size() * 2), const_cast<wchar_t*>(payload.data())};
    DWORD_PTR result = 0;
    check(SendMessageTimeoutW(hwnd, WM_COPYDATA, 0, reinterpret_cast<LPARAM>(&data),
        SMTO_ABORTIFHUNG | SMTO_BLOCK, 1500, &result) != 0, "message timeout/failure");
    return static_cast<LRESULT>(result);
}
}
int wmain(int argc, wchar_t** argv) {
    if (argc != 2) { fwprintf(stderr, L"Usage: TsfProbe.exe ABSOLUTE_DLL_PATH\n"); return 2; }
    HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(hr)) return 3;
    HMODULE dll = LoadLibraryExW(argv[1], nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!dll) { CoUninitialize(); return 4; }
    int code = 0;
    bool safeToUnload = false;
    try {
        auto get = reinterpret_cast<HRESULT (STDAPICALLTYPE*)(REFCLSID, REFIID, void**)>(GetProcAddress(dll, "DllGetClassObject"));
        auto unload = reinterpret_cast<HRESULT (STDAPICALLTYPE*)()>(GetProcAddress(dll, "DllCanUnloadNow"));
        check(get && unload, "missing exports");
        check(unload() == S_OK, "initial unload state");
        {
            ComPtr<IClassFactory> factory;
            check(SUCCEEDED(get(kServiceClsid, IID_IClassFactory, reinterpret_cast<void**>(factory.put()))), "class factory");
            check(unload() == S_FALSE, "factory lifetime");
            check(SUCCEEDED(factory->LockServer(TRUE)), "server lock");
            check(SUCCEEDED(factory->LockServer(FALSE)), "server unlock");
            ComPtr<ITfTextInputProcessor> service;
            check(SUCCEEDED(factory->CreateInstance(nullptr, IID_ITfTextInputProcessor,
                reinterpret_cast<void**>(service.put()))), "service creation");
            ComPtr<ITfThreadMgr> manager;
            check(SUCCEEDED(CoCreateInstance(CLSID_TF_ThreadMgr, nullptr, CLSCTX_INPROC_SERVER,
                IID_ITfThreadMgr, reinterpret_cast<void**>(manager.put()))), "private thread manager");
            TfClientId client;
            check(SUCCEEDED(manager->Activate(&client)), "manager activation");
            struct ManagerGuard { ITfThreadMgr* p; ~ManagerGuard() { p->Deactivate(); } } managerGuard{manager.p};
            ComPtr<ITfDocumentMgr> document;
            ComPtr<ITfContext> context;
            TfEditCookie cookie;
            check(SUCCEEDED(manager->CreateDocumentMgr(document.put())), "private document");
            check(SUCCEEDED(document->CreateContext(client, 0, nullptr, context.put(), &cookie)), "private context");
            check(SUCCEEDED(document->Push(context.p)), "private context push");
            check(SUCCEEDED(service->Activate(manager.p, client)), "service activation");
            struct ServiceGuard { ITfTextInputProcessor* p; ~ServiceGuard() { p->Deactivate(); } } serviceGuard{service.p};
            HWND hwnd = bridge();
            check(hwnd != nullptr, "bridge creation");
            check(FAILED(service->Activate(manager.p, client)), "duplicate activation rejected");
            const std::vector<std::vector<wchar_t>> invalid = {
                {}, {0}, {L'a'}, {L'a',0,L'b',0}, {L'\n',0}, {0x7f,0}, {0x80,0},
                {0x9f,0}, {0x2028,0}, {0x2029,0}, {0xd800,0}, {0xdc00,0}, {0xd800,L'x',0}
            };
            for (const auto& value : invalid) check(send(hwnd, value) == InvalidPayload, "invalid payload rejection");
            check(send(hwnd, {L'x',0}, 0) == InvalidPayload, "tag rejection");
            std::vector<wchar_t> tooLong(65537, L'x'); tooLong.back() = 0;
            check(send(hwnd, tooLong) == InvalidPayload, "length rejection");
            DWORD foregroundPid = 0;
            DWORD foregroundTid = GetWindowThreadProcessId(GetForegroundWindow(), &foregroundPid);
            // No foreground/focus calls, private desktop switching, or production bypass.
            check(foregroundPid != GetCurrentProcessId() || foregroundTid != GetCurrentThreadId(), "probe unexpectedly foreground; no valid send attempted");
            check(send(hwnd, {L'x',0}) == ForegroundMismatch, "foreground rejection");
            check(send(hwnd, {0xd83d,0xde00,0}) == ForegroundMismatch, "valid surrogate pair reaches foreground guard");
            check(SUCCEEDED(service->Deactivate()), "service deactivation");
            check(!bridge(), "bridge destruction");
            check(SUCCEEDED(service->Deactivate()), "idempotent deactivation");
            check(SUCCEEDED(service->Activate(manager.p, client)), "service reactivation");
            check(bridge() != nullptr, "bridge recreation");
            check(SUCCEEDED(service->Deactivate()), "final deactivation");
            document->Pop(TF_POPF_ALL);
        }
        check(unload() == S_OK, "final object count");
        safeToUnload = true;
        puts("PASS: direct factory, own TSF context, bridge lifecycle, payload/foreground rejection");
        puts("UNVERIFIED: real insertion, application compatibility, read-only/busy edit-session behavior");
    } catch (const std::exception& e) { fprintf(stderr, "FAIL: %s\n", e.what()); code = 1; }
    // On a failed lifecycle assertion, leave the module loaded until process exit.
    if (safeToUnload) FreeLibrary(dll);
    CoUninitialize();
    return code;
}
