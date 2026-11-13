#include "protocol.h"
#include <atomic>
#include <cstring>
#include <new>
#include <string>

namespace {
HINSTANCE instance;
std::atomic<long> objects{0}, locks{0};

bool foregroundMatches(DWORD thread) {
    DWORD pid = 0;
    DWORD tid = GetWindowThreadProcessId(GetForegroundWindow(), &pid);
    return tid == thread && pid == GetCurrentProcessId();
}

bool decode(const COPYDATASTRUCT* data, std::wstring& text) {
    if (!data || data->dwData != kCopyDataTag || !data->lpData ||
        data->cbData < 4 || data->cbData > 65536 * sizeof(wchar_t) || data->cbData % 2) return false;
    // WM_COPYDATA buffers need not be aligned; copy before inspecting UTF-16.
    text.resize(data->cbData / 2);
    std::memcpy(text.data(), data->lpData, data->cbData);
    if (text.back() != 0) return false;
    text.pop_back();
    for (size_t i = 0; i < text.size(); ++i) {
        unsigned c = text[i];
        if (c < 0x20 || (c >= 0x7f && c <= 0x9f) || c == 0x2028 || c == 0x2029) return false;
        if (c >= 0xd800 && c <= 0xdbff) {
            if (++i == text.size() || text[i] < 0xdc00 || text[i] > 0xdfff) return false;
        } else if (c >= 0xdc00 && c <= 0xdfff) return false;
    }
    return !text.empty();
}

class Service;
class EditSession final : public ITfEditSession {
    std::atomic<ULONG> refs{1};
    Service* service;
    ITfContext* context;
    std::wstring text;
public:
    Result result = Busy;
    bool completed = false;
    bool armed = true;
    EditSession(Service* s, ITfContext* c, std::wstring value);
    ~EditSession();
    STDMETHODIMP QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_ITfEditSession) return E_NOINTERFACE;
        *out = static_cast<ITfEditSession*>(this); AddRef(); return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return ++refs; }
    STDMETHODIMP_(ULONG) Release() override { ULONG n = --refs; if (!n) delete this; return n; }
    STDMETHODIMP DoEditSession(TfEditCookie cookie) override;
};

class Service final : public ITfTextInputProcessor {
    std::atomic<ULONG> refs{1};
    ITfThreadMgr* manager = nullptr;
    TfClientId client = TF_CLIENTID_NULL;
    DWORD thread = 0;
    HWND window = nullptr;
    bool busy = false;
    bool active = false;
    static LRESULT CALLBACK windowProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
        auto* self = reinterpret_cast<Service*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
        if (msg == WM_NCCREATE) {
            self = static_cast<Service*>(reinterpret_cast<CREATESTRUCTW*>(lp)->lpCreateParams);
            self->AddRef();
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        }
        if (msg == WM_NCDESTROY && self) {
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
            self->window = nullptr;
            LRESULT result = DefWindowProcW(hwnd, msg, wp, lp);
            self->Release();
            return result;
        }
        if (msg == WM_COPYDATA && self) {
            self->AddRef();
            LRESULT result = InternalFailure;
            try { result = self->receive(reinterpret_cast<COPYDATASTRUCT*>(lp)); } catch (...) {}
            self->Release();
            return result;
        }
        return DefWindowProcW(hwnd, msg, wp, lp);
    }
    LRESULT receive(const COPYDATASTRUCT* data) {
        std::wstring text;
        if (!decode(data, text)) return InvalidPayload;
        if (!foregroundMatches(thread)) return ForegroundMismatch;
        if (!active || !manager) return NoContext;
        if (busy) return Busy;
        busy = true;
        struct Reset { bool& flag; ~Reset() { flag = false; } } reset{busy};
        ComPtr<ITfThreadMgr> mgr;
        mgr.p = manager; mgr->AddRef();
        ComPtr<ITfDocumentMgr> doc;
        ComPtr<ITfContext> context;
        if (FAILED(mgr->GetFocus(doc.put())) || !doc.p ||
            FAILED(doc->GetTop(context.put())) || !context.p) return NoContext;
        TF_STATUS status{};
        if (FAILED(context->GetStatus(&status)) || (status.dwStaticFlags & TF_SD_READONLY)) return NoContext;
        auto* session = new (std::nothrow) EditSession(this, context.p, std::move(text));
        if (!session) return InternalFailure;
        HRESULT sessionHr = E_FAIL;
        HRESULT hr = context->RequestEditSession(client, session, TF_ES_SYNC | TF_ES_READWRITE, &sessionHr);
        session->armed = false;
        // No async request or retry: an uncompleted edit can never produce an ACK.
        Result result = session->completed ? session->result : Busy;
        if (hr == TF_E_READONLY || sessionHr == TF_E_READONLY) result = NoContext;
        session->Release();
        return result;
    }
public:
    Service() { ++objects; }
    ~Service() { if (manager) manager->Release(); --objects; }
    bool canInsert() const { return active && foregroundMatches(thread); }
    bool hasContext(ITfContext* expected) const {
        if (!active || !manager) return false;
        ComPtr<ITfDocumentMgr> doc;
        ComPtr<ITfContext> current;
        if (FAILED(manager->GetFocus(doc.put())) || !doc.p ||
            FAILED(doc->GetTop(current.put())) || !current.p) return false;
        ComPtr<IUnknown> currentIdentity;
        ComPtr<IUnknown> expectedIdentity;
        return SUCCEEDED(current->QueryInterface(IID_IUnknown, reinterpret_cast<void**>(currentIdentity.put()))) &&
            SUCCEEDED(expected->QueryInterface(IID_IUnknown, reinterpret_cast<void**>(expectedIdentity.put()))) &&
            currentIdentity.p == expectedIdentity.p;
    }
    STDMETHODIMP QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_ITfTextInputProcessor) return E_NOINTERFACE;
        *out = static_cast<ITfTextInputProcessor*>(this); AddRef(); return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return ++refs; }
    STDMETHODIMP_(ULONG) Release() override { ULONG n = --refs; if (!n) delete this; return n; }
    STDMETHODIMP Activate(ITfThreadMgr* mgr, TfClientId id) override {
        if (!mgr) return E_INVALIDARG;
        if (manager || window || busy) return E_UNEXPECTED;
        WNDCLASSEXW wc{sizeof(wc)};
        wc.lpfnWndProc = windowProc; wc.hInstance = instance; wc.lpszClassName = kBridgeClass;
        if (!RegisterClassExW(&wc)) {
            if (GetLastError() != ERROR_CLASS_ALREADY_EXISTS) return HRESULT_FROM_WIN32(GetLastError());
            WNDCLASSEXW existing{sizeof(existing)};
            if (!GetClassInfoExW(instance, kBridgeClass, &existing) || existing.lpfnWndProc != windowProc) return E_FAIL;
        }
        // Only one bridge per apartment, otherwise discovery would be ambiguous.
        HWND other = nullptr;
        while ((other = FindWindowExW(HWND_MESSAGE, other, kBridgeClass, nullptr))) {
            DWORD pid = 0;
            if (GetWindowThreadProcessId(other, &pid) == GetCurrentThreadId() && pid == GetCurrentProcessId()) return E_UNEXPECTED;
        }
        thread = GetCurrentThreadId(); client = id;
        manager = mgr; manager->AddRef();
        window = CreateWindowExW(0, kBridgeClass, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, instance, this);
        if (!window) { manager->Release(); manager = nullptr; return E_FAIL; }
        active = true;
        return S_OK;
    }
    STDMETHODIMP Deactivate() override {
        if (manager && GetCurrentThreadId() != thread) return RPC_E_WRONG_THREAD;
        active = false;
        if (window) DestroyWindow(window);
        if (manager) { auto* old = manager; manager = nullptr; old->Release(); }
        client = TF_CLIENTID_NULL;
        return S_OK;
    }
};

EditSession::EditSession(Service* s, ITfContext* c, std::wstring value)
    : service(s), context(c), text(std::move(value)) { ++objects; service->AddRef(); context->AddRef(); }
EditSession::~EditSession() { context->Release(); service->Release(); --objects; }
STDMETHODIMP EditSession::DoEditSession(TfEditCookie cookie) {
    // A synchronous request must not become an asynchronously executable send.
    if (!armed || completed) return E_UNEXPECTED;
    completed = true;
    if (!service->canInsert()) { result = ForegroundMismatch; return S_OK; }
    if (!service->hasContext(context)) { result = NoContext; return S_OK; }
    TF_STATUS status{};
    if (FAILED(context->GetStatus(&status)) || (status.dwStaticFlags & TF_SD_READONLY)) {
        result = NoContext; return S_OK;
    }
    ComPtr<ITfInsertAtSelection> insert;
    if (FAILED(context->QueryInterface(IID_ITfInsertAtSelection, reinterpret_cast<void**>(insert.put())))) {
        result = NoContext; return S_OK;
    }
    if (!service->hasContext(context)) { result = NoContext; return S_OK; }
    if (!service->canInsert()) { result = ForegroundMismatch; return S_OK; }
    ComPtr<ITfRange> range;
    HRESULT hr = insert->InsertTextAtSelection(cookie, 0, text.data(), static_cast<LONG>(text.size()), range.put());
    result = hr == S_OK ? Inserted :
        (hr == TF_E_READONLY || hr == E_ACCESSDENIED ? NoContext :
        (hr == TF_E_LOCKED ? Busy : InternalFailure));
    return S_OK;
}

class Factory final : public IClassFactory {
    std::atomic<ULONG> refs{1};
public:
    Factory() { ++objects; }
    ~Factory() { --objects; }
    STDMETHODIMP QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_IClassFactory) return E_NOINTERFACE;
        *out = static_cast<IClassFactory*>(this); AddRef(); return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return ++refs; }
    STDMETHODIMP_(ULONG) Release() override { ULONG n = --refs; if (!n) delete this; return n; }
    STDMETHODIMP CreateInstance(IUnknown* outer, REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* service = new (std::nothrow) Service;
        if (!service) return E_OUTOFMEMORY;
        HRESULT hr = service->QueryInterface(iid, out); service->Release(); return hr;
    }
    STDMETHODIMP LockServer(BOOL lock) override {
        if (lock) ++locks;
        else { long n = locks.load(); do { if (!n) return E_UNEXPECTED; } while (!locks.compare_exchange_weak(n, n - 1)); }
        return S_OK;
    }
};
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) instance = module;
    return TRUE;
}
STDAPI DllGetClassObject(REFCLSID clsid, REFIID iid, void** out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    if (clsid != kServiceClsid) return CLASS_E_CLASSNOTAVAILABLE;
    auto* factory = new (std::nothrow) Factory;
    if (!factory) return E_OUTOFMEMORY;
    HRESULT hr = factory->QueryInterface(iid, out); factory->Release(); return hr;
}
STDAPI DllCanUnloadNow() {
    if (objects.load() != 0 || locks.load() != 0) return S_FALSE;
    // Remove the callback-bearing class before COM unloads this module, outside DllMain.
    UnregisterClassW(kBridgeClass, instance);
    return S_OK;
}
