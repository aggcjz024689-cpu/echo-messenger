#include <windows.h>
#include <wil/com.h>
#include <WebView2.h>
#include <objbase.h>

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")

static wil::com_ptr<ICoreWebView2> webview;
static wil::com_ptr<ICoreWebView2Controller> controller;
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
    
    WNDCLASS wc = {};
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInstance;
    wc.lpszClassName = CLASS_NAME;
    wc.hIcon = LoadIcon(hInstance, MAKEINTRESOURCE(101));
    wc.hbrBackground = (HBRUSH)GetStockObject(BLACK_BRUSH);
    
    RegisterClass(&wc);
    
    HWND hwnd = CreateWindowEx(
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

void InitializeWebView2(HWND hwnd) {
    CreateCoreWebView2EnvironmentWithOptions(
        nullptr, nullptr, nullptr,
        Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>(
            [hwnd](HRESULT result, ICoreWebView2Environment* env) -> HRESULT {
                env->CreateCoreWebView2Controller(
                    hwnd,
                    Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>(
                        [hwnd](HRESULT result, ICoreWebView2Controller* ctrl) -> HRESULT {
                            controller = ctrl;
                            controller->get_CoreWebView2(&webview);
                            
                            RECT bounds;
                            GetClientRect(hwnd, &bounds);
                            controller->put_Bounds(bounds);
                            
                            webview->Navigate(L"https://echo-messenger-wko2.onrender.com");
                            return S_OK;
                        }
                    ).Get());
                return S_OK;
            }
        ).Get());
}

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, PSTR lpCmdLine, int nCmdShow) {
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    
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