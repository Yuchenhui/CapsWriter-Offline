#pragma once
#include <windows.h>
#include <msctf.h>

inline constexpr CLSID kServiceClsid = {0xb96c3f71,0xdb8a,0x4fba,{0x9f,0x27,0x8f,0x2d,0x7f,0x81,0x9d,0x7a}};
inline constexpr GUID kProfileGuid = {0xa41ccb7e,0x34bb,0x4206,{0xa4,0xa7,0xf1,0x81,0x00,0x41,0x83,0x59}};
inline constexpr wchar_t kClsidText[] = L"{B96C3F71-DB8A-4FBA-9F27-8F2D7F819D7A}";
inline constexpr wchar_t kBridgeClass[] = L"CapsWriter.TsfBridge.V1";
inline constexpr wchar_t kName[] = L"CapsWriter Voice";
inline constexpr LANGID kLanguage = 0x0804;
inline constexpr ULONG_PTR kCopyDataTag = 0x43575453;
enum Result : LRESULT { Inserted=1, ForegroundMismatch=2, NoContext=3, Busy=4, InternalFailure=5, InvalidPayload=6 };

template<class T> class ComPtr {
public:
    T* p = nullptr;
    ~ComPtr() { if (p) p->Release(); }
    ComPtr() = default;
    ComPtr(const ComPtr&) = delete;
    ComPtr& operator=(const ComPtr&) = delete;
    T* operator->() const { return p; }
    T** put() { return &p; }
};
