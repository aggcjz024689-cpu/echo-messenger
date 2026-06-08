#define UNICODE
#define _UNICODE
#include <windows.h>
#include <objbase.h>
#include <wrl/client.h>
#include <wrl/event.h>
#include <string>

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")

using namespace Microsoft::WRL;

// Типы функций из WebView2
typedef HRESULT (WINAPI *CreateCoreWebView2EnvironmentWithOptionsFn)(
    PCWSTR browserExecutableFolder,
    PCWSTR userDataFolder,
    ICoreWebView2EnvironmentOptions* environmentOptions,
    ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler* environmentCreatedHandler
);

static CreateCoreWebView2EnvironmentWithOptionsFn pfnCreateCoreWebView2Environment = nullptr;
static HMODULE hWebView2Loader = nullptr;

static ComPtr<ICoreWebView2> webview;
static ComPtr<ICoreWebView2Controller> controller;
static HWND mainHwnd = nullptr;

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
        case WM_SIZE:
            if (controller) {
                RECT bounds;
                GetClientRect(hwnd, &bounds);
                controller->put_Bounds(bounds);
            }
            return 0;
        case WM_CLOSE:
            DestroyWindow(hwnd);
            return 0;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
        default:
            return DefWindowProc(hwnd, msg, wParam, lParam);
    }
}

HWND CreateMainWindow(HINSTANCE hInstance, int nCmdShow) {
    const wchar_t CLASS_NAME[] = L"EchoMessengerWindow";
    
    WNDCLASSW wc = {};
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInstance;
    wc.lpszClassName = CLASS_NAME;
    wc.hIcon = LoadIconW(hInstance, MAKEINTRESOURCE(101));
    wc.hbrBackground = (HBRUSH)GetStockObject(BLACK_BRUSH);
    
    RegisterClassW(&wc);
    
    HWND hwnd = CreateWindowExW(
        0, CLASS_NAME, L"ЭХО Мессенджер",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        CW_USEDEFAULT, CW_USEDEFAULT, 1200, 800,
        NULL, NULL, hInstance, NULL
    );
    
    if (hwnd) {
        ShowWindow(hwnd, nCmdShow);
        UpdateWindow(hwnd);
    }
    return hwnd;
}

bool LoadWebView2Functions() {
    hWebView2Loader = LoadLibraryW(L"WebView2Loader.dll");
    if (!hWebView2Loader) return false;
    
    pfnCreateCoreWebView2Environment = (CreateCoreWebView2EnvironmentWithOptionsFn)GetProcAddress(hWebView2Loader, "CreateCoreWebView2EnvironmentWithOptions");
    if (!pfnCreateCoreWebView2Environment) return false;
    
    return true;
}

void InitializeWebView2(HWND hwnd) {
    if (!pfnCreateCoreWebView2Environment) return;
    
    pfnCreateCoreWebView2Environment(
        nullptr, nullptr, nullptr,
        Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>(
            [hwnd](HRESULT result, ICoreWebView2Environment* env) -> HRESULT {
                env->CreateCoreWebView2Controller(
                    hwnd,
                    Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>(
                        [](HRESULT result, ICoreWebView2Controller* ctrl) -> HRESULT {
                            controller = ctrl;
                            controller->get_CoreWebView2(&webview);
                            
                            webview->Navigate(L"https://echo-messenger-wko2.onrender.com");
                            return S_OK;
                        }
                    ).Get());
                return S_OK;
            }
        ).Get());
}

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, PWSTR pCmdLine, int nCmdShow) {
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    
    if (!LoadWebView2Functions()) {
        MessageBoxW(NULL, L"Не удалось загрузить WebView2Loader.dll\n\nУстановите Microsoft Edge WebView2 Runtime.", L"Ошибка", MB_ICONERROR);
        return 1;
    }
    
    mainHwnd = CreateMainWindow(hInstance, nCmdShow);
    if (!mainHwnd) return 1;
    
    InitializeWebView2(mainHwnd);
    
    MSG msg = {};
    while (GetMessage(&msg, NULL, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }
    
    CoUninitialize();
    return 0;
}
