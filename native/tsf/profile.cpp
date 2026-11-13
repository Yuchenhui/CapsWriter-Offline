#include "protocol.h"
#include <cstdio>
#include <cwchar>

// COM server mapping and ownership checks are performed by the explicit scripts.
// This executable touches only this service's one profile and keyboard category.
int wmain(int argc, wchar_t** argv) {
    if (argc != 3 || (wcscmp(argv[1], L"register") && wcscmp(argv[1], L"unregister"))) {
        fwprintf(stderr, L"Usage: TsfProfile.exe register|unregister ABSOLUTE_DLL_PATH\n"); return 2;
    }
    HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(hr)) return 3;
    int exitCode = 0;
    {
        ComPtr<ITfInputProcessorProfileMgr> profiles;
        ComPtr<ITfCategoryMgr> categories;
        hr = CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER,
            IID_ITfInputProcessorProfileMgr, reinterpret_cast<void**>(profiles.put()));
        if (SUCCEEDED(hr)) hr = CoCreateInstance(CLSID_TF_CategoryMgr, nullptr, CLSCTX_INPROC_SERVER,
            IID_ITfCategoryMgr, reinterpret_cast<void**>(categories.put()));
        if (SUCCEEDED(hr) && !wcscmp(argv[1], L"register")) {
            hr = profiles->RegisterProfile(kServiceClsid, kLanguage, kProfileGuid, kName,
                static_cast<ULONG>(wcslen(kName)), argv[2], static_cast<ULONG>(wcslen(argv[2])),
                0, nullptr, 0, FALSE, 0);
            if (SUCCEEDED(hr)) {
                hr = categories->RegisterCategory(kServiceClsid, GUID_TFCAT_TIP_KEYBOARD, kServiceClsid);
                if (SUCCEEDED(hr)) {
                    ComPtr<ITfInputProcessorProfiles> userProfiles;
                    hr = profiles->QueryInterface(IID_ITfInputProcessorProfiles, reinterpret_cast<void**>(userProfiles.put()));
                    if (SUCCEEDED(hr)) hr = userProfiles->EnableLanguageProfile(kServiceClsid, kLanguage, kProfileGuid, TRUE);
                }
                if (FAILED(hr)) {
                    HRESULT categoryRollback = categories->UnregisterCategory(kServiceClsid, GUID_TFCAT_TIP_KEYBOARD, kServiceClsid);
                    fwprintf(stderr, L"Category rollback HRESULT=0x%08lX\n", static_cast<unsigned long>(categoryRollback));
                    HRESULT rollback = profiles->UnregisterProfile(kServiceClsid, kLanguage, kProfileGuid, 0);
                    fwprintf(stderr, L"Profile rollback HRESULT=0x%08lX\n", static_cast<unsigned long>(rollback));
                }
            }
        } else if (SUCCEEDED(hr)) {
            // Never Unregister(clsid): it could delete another profile under this CLSID.
            HRESULT categoryHr = categories->UnregisterCategory(kServiceClsid, GUID_TFCAT_TIP_KEYBOARD, kServiceClsid);
            HRESULT profileHr = profiles->UnregisterProfile(kServiceClsid, kLanguage, kProfileGuid, 0);
            hr = FAILED(profileHr) ? profileHr : categoryHr;
        }
        wprintf(L"TSF metadata HRESULT=0x%08lX\n", static_cast<unsigned long>(hr));
        if (FAILED(hr)) exitCode = 1;
    }
    CoUninitialize();
    return exitCode;
}
