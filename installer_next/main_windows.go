//go:build windows

package main

import (
    "fmt"
    "os"
    "runtime"
    "strings"
    "sync"
    "syscall"
    "unsafe"
)

const (
    windowW = 860
    windowH = 540
    sidebarW = 246

    wmCreate = 0x0001
    wmDestroy = 0x0002
    wmClose = 0x0010
    wmPaint = 0x000F
    wmCommand = 0x0111
    wmCtlColorStatic = 0x0138
    wmSetFont = 0x0030
    wmApp = 0x8000
    msgProgress = wmApp + 1
    msgWorkDone = wmApp + 2

    wsOverlapped = 0x00000000
    wsCaption = 0x00C00000
    wsSysMenu = 0x00080000
    wsMinimizeBox = 0x00020000
    wsChild = 0x40000000
    wsVisible = 0x10000000
    wsTabStop = 0x00010000

    bsPushButton = 0x00000000
    bsAutoCheckbox = 0x00000003
    ssLeft = 0x00000000

    swHide = 0
    swShow = 5
    swShowNormal = 1

    bnClicked = 0
    bstChecked = 1
    bmGetCheck = 0x00F0

    pbmSetRange32 = 0x0406
    pbmSetPos = 0x0402
    pbsSmooth = 0x01

    transparent = 1
    dtLeft = 0x0000
    dtWordBreak = 0x0010
    colorWindow = 5

    idBack = 1001
    idNext = 1002
    idCancel = 1003
    idDesktop = 1010
    idRemoveData = 1011
    idRunAfter = 1012
)

const (
    pageWelcome = iota
    pageOptions
    pageProgress
    pageFinish
)

type point struct{ X, Y int32 }
type rect struct{ Left, Top, Right, Bottom int32 }
type msg struct {
    Hwnd uintptr
    Message uint32
    WParam uintptr
    LParam uintptr
    Time uint32
    Pt point
    Private uint32
}
type paintStruct struct {
    Hdc uintptr
    Erase int32
    RcPaint rect
    Restore int32
    IncUpdate int32
    Reserved [32]byte
}
type wndClassEx struct {
    CbSize uint32
    Style uint32
    LpfnWndProc uintptr
    CbClsExtra int32
    CbWndExtra int32
    HInstance uintptr
    HIcon uintptr
    HCursor uintptr
    HbrBackground uintptr
    LpszMenuName *uint16
    LpszClassName *uint16
    HIconSm uintptr
}

type appUI struct {
    hwnd uintptr
    page int
    uninstall bool
    working bool
    failed bool
    appPath string

    sidebarBrand uintptr
    sidebarVersion uintptr
    title uintptr
    desc uintptr
    detail uintptr
    desktop uintptr
    removeData uintptr
    runAfter uintptr
    progress uintptr
    progressText uintptr
    back uintptr
    next uintptr
    cancel uintptr

    titleFont uintptr
    bodyFont uintptr
    smallFont uintptr

    statusMu sync.Mutex
    status string
    workErr error
}

var ui appUI

var (
    user32 = syscall.NewLazyDLL("user32.dll")
    kernel32 = syscall.NewLazyDLL("kernel32.dll")
    gdi32 = syscall.NewLazyDLL("gdi32.dll")
    comctl32 = syscall.NewLazyDLL("comctl32.dll")

    pRegisterClassExW = user32.NewProc("RegisterClassExW")
    pCreateWindowExW = user32.NewProc("CreateWindowExW")
    pDefWindowProcW = user32.NewProc("DefWindowProcW")
    pShowWindow = user32.NewProc("ShowWindow")
    pUpdateWindow = user32.NewProc("UpdateWindow")
    pGetMessageW = user32.NewProc("GetMessageW")
    pTranslateMessage = user32.NewProc("TranslateMessage")
    pDispatchMessageW = user32.NewProc("DispatchMessageW")
    pPostQuitMessage = user32.NewProc("PostQuitMessage")
    pDestroyWindow = user32.NewProc("DestroyWindow")
    pPostMessageW = user32.NewProc("PostMessageW")
    pSendMessageW = user32.NewProc("SendMessageW")
    pSetWindowTextW = user32.NewProc("SetWindowTextW")
    pEnableWindow = user32.NewProc("EnableWindow")
    pBeginPaint = user32.NewProc("BeginPaint")
    pEndPaint = user32.NewProc("EndPaint")
    pFillRect = user32.NewProc("FillRect")
    pGetClientRect = user32.NewProc("GetClientRect")
    pSetBkMode = gdi32.NewProc("SetBkMode")
    pSetTextColor = gdi32.NewProc("SetTextColor")
    pCreateSolidBrush = gdi32.NewProc("CreateSolidBrush")
    pCreateFontW = gdi32.NewProc("CreateFontW")
    pGetModuleHandleW = kernel32.NewProc("GetModuleHandleW")
)

func wstr(s string) *uint16 {
    p, _ := syscall.UTF16PtrFromString(s)
    return p
}

func rgb(r, g, b byte) uintptr {
    return uintptr(r) | uintptr(g)<<8 | uintptr(b)<<16
}

func createFont(size int, weight int) uintptr {
    h, _, _ := pCreateFontW.Call(
        uintptr(int32(-size)), 0, 0, 0, uintptr(weight), 0, 0, 0,
        1, 0, 0, 5, 0, uintptr(unsafe.Pointer(wstr("Segoe UI"))),
    )
    return h
}

func createControl(class, text string, style uint32, x, y, w, h, id int) uintptr {
    hwnd, _, _ := pCreateWindowExW.Call(
        0,
        uintptr(unsafe.Pointer(wstr(class))),
        uintptr(unsafe.Pointer(wstr(text))),
        uintptr(style),
        uintptr(x), uintptr(y), uintptr(w), uintptr(h),
        ui.hwnd, uintptr(id), 0, 0,
    )
    return hwnd
}

func setText(hwnd uintptr, text string) {
    pSetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(wstr(text))))
}

func show(hwnd uintptr, visible bool) {
    if hwnd == 0 { return }
    cmd := uintptr(swHide)
    if visible { cmd = swShow }
    pShowWindow.Call(hwnd, cmd)
}

func setFont(hwnd, font uintptr) {
    if hwnd != 0 && font != 0 {
        pSendMessageW.Call(hwnd, wmSetFont, font, 1)
    }
}

func isChecked(hwnd uintptr) bool {
    v, _, _ := pSendMessageW.Call(hwnd, bmGetCheck, 0, 0)
    return v == bstChecked
}

func setProgress(percent int) {
    if percent < 0 { percent = 0 }
    if percent > 100 { percent = 100 }
    pSendMessageW.Call(ui.progress, pbmSetPos, uintptr(percent), 0)
}

func renderPage() {
    install := !ui.uninstall
    show(ui.desktop, install && ui.page == pageOptions)
    show(ui.removeData, ui.uninstall && ui.page == pageOptions)
    show(ui.progress, ui.page == pageProgress)
    show(ui.progressText, ui.page == pageProgress)
    show(ui.runAfter, install && ui.page == pageFinish && !ui.failed)
    show(ui.back, ui.page == pageOptions && !ui.working)
    show(ui.cancel, ui.page != pageFinish && !ui.working)

    if ui.page == pageWelcome {
        setText(ui.title, func() string { if ui.uninstall { return "Удаление LinkVideo.Helper" }; return "Добро пожаловать" }())
        if ui.uninstall {
            setText(ui.desc, "Мастер удалит LinkVideo.Helper с этого компьютера. На следующем шаге можно выбрать, сохранять ли настройки приложения.")
            setText(ui.detail, "Ваши выгруженные видеозаписи в папке «Видео» не удаляются.")
            setText(ui.next, "Продолжить")
        } else {
            setText(ui.desc, "Установщик LinkVideo.Helper подготовит рабочую среду, обновит существующую версию без потери настроек и зарегистрирует отдельный деинсталлятор.")
            setText(ui.detail, "Windows x64 · установка в Program Files · существующие настройки сохраняются")
            setText(ui.next, "Продолжить")
        }
    } else if ui.page == pageOptions {
        if ui.uninstall {
            setText(ui.title, "Что удалить?")
            setText(ui.desc, "Файлы программы будут удалены всегда. Настройки, B2O-сессия и локальный кэш удаляются только по вашему выбору.")
            setText(ui.detail, "Снятый флажок позволяет позже установить Helper заново с прежними настройками.")
            setText(ui.next, "Удалить")
        } else {
            setText(ui.title, "Параметры установки")
            setText(ui.desc, "Программа будет установлена в C:\\Program Files\\LinkVideo.Helper. Предыдущая Inno-версия будет корректно переведена на новый установщик.")
            setText(ui.detail, "Настройки и сохранённые учётные данные не перезаписываются.")
            setText(ui.next, "Установить")
        }
    } else if ui.page == pageProgress {
        setText(ui.title, func() string { if ui.uninstall { return "Удаление…" }; return "Установка…" }())
        setText(ui.desc, "Не закрывайте окно до завершения операции.")
        setText(ui.detail, "")
        show(ui.next, false)
    } else if ui.page == pageFinish {
        show(ui.next, true)
        show(ui.cancel, false)
        show(ui.back, false)
        if ui.failed {
            setText(ui.title, "Операция не завершена")
            setText(ui.desc, "Windows вернула ошибку. Файлы публичного релиза не изменялись — это тест нового установщика.")
            ui.statusMu.Lock(); detail := ui.status; ui.statusMu.Unlock()
            setText(ui.detail, detail)
            setText(ui.next, "Закрыть")
        } else if ui.uninstall {
            setText(ui.title, "LinkVideo.Helper удалён")
            setText(ui.desc, "Удаление завершено. Окно можно закрыть.")
            setText(ui.detail, "Спасибо за использование LinkVideo.Helper.")
            setText(ui.next, "Готово")
        } else {
            setText(ui.title, "Установка завершена")
            setText(ui.desc, fmt.Sprintf("LinkVideo.Helper %s готов к работе.", version))
            setText(ui.detail, "Ярлыки и запись удаления Windows обновлены.")
            setText(ui.next, "Готово")
        }
    }
    show(ui.next, ui.page != pageProgress)
    pUpdateWindow.Call(ui.hwnd)
}

func startWork() {
    ui.working = true
    ui.failed = false
    ui.page = pageProgress
    pEnableWindow.Call(ui.back, 0)
    pEnableWindow.Call(ui.cancel, 0)
    renderPage()
    go func() {
        progress := func(percent int, status string) {
            ui.statusMu.Lock(); ui.status = status; ui.statusMu.Unlock()
            pPostMessageW.Call(ui.hwnd, msgProgress, uintptr(percent), 0)
        }
        var err error
        if ui.uninstall {
            err = uninstallProduct(isChecked(ui.removeData), progress)
        } else {
            ui.appPath, err = installProduct(installOptions{DesktopShortcut: isChecked(ui.desktop)}, progress)
        }
        ui.statusMu.Lock()
        ui.workErr = err
        if err != nil { ui.status = err.Error() }
        ui.statusMu.Unlock()
        fail := uintptr(0)
        if err != nil { fail = 1 }
        pPostMessageW.Call(ui.hwnd, msgWorkDone, fail, 0)
    }()
}

func onNext() {
    if ui.working { return }
    switch ui.page {
    case pageWelcome:
        ui.page = pageOptions
        renderPage()
    case pageOptions:
        startWork()
    case pageFinish:
        if !ui.failed && !ui.uninstall && isChecked(ui.runAfter) && ui.appPath != "" {
            _ = launchApplication(ui.appPath)
        }
        pDestroyWindow.Call(ui.hwnd)
    }
}

func onBack() {
    if ui.working { return }
    if ui.page == pageOptions {
        ui.page = pageWelcome
        renderPage()
    }
}

func wndProc(hwnd uintptr, message uint32, wParam, lParam uintptr) uintptr {
    switch message {
    case wmCreate:
        ui.hwnd = hwnd
        ui.titleFont = createFont(28, 600)
        ui.bodyFont = createFont(17, 400)
        ui.smallFont = createFont(14, 400)

        ui.sidebarBrand = createControl("STATIC", "LinkVideo", wsChild|wsVisible|ssLeft, 28, 44, 190, 34, 0)
        ui.sidebarVersion = createControl("STATIC", "HELPER  ·  "+version, wsChild|wsVisible|ssLeft, 28, 84, 190, 26, 0)
        ui.title = createControl("STATIC", "", wsChild|wsVisible|ssLeft, sidebarW+34, 52, 540, 44, 0)
        ui.desc = createControl("STATIC", "", wsChild|wsVisible|ssLeft, sidebarW+34, 112, 530, 86, 0)
        ui.detail = createControl("STATIC", "", wsChild|wsVisible|ssLeft, sidebarW+34, 210, 530, 62, 0)
        ui.desktop = createControl("BUTTON", "Создать ярлык на рабочем столе", wsChild|wsVisible|wsTabStop|bsAutoCheckbox, sidebarW+34, 292, 420, 28, idDesktop)
        ui.removeData = createControl("BUTTON", "Удалить настройки, B2O-сессию и локальный кэш", wsChild|wsVisible|wsTabStop|bsAutoCheckbox, sidebarW+34, 292, 500, 28, idRemoveData)
        ui.runAfter = createControl("BUTTON", "Запустить LinkVideo.Helper", wsChild|wsVisible|wsTabStop|bsAutoCheckbox, sidebarW+34, 292, 420, 28, idRunAfter)
        ui.progress = createControl("msctls_progress32", "", wsChild|pbsSmooth, sidebarW+34, 292, 500, 22, 0)
        ui.progressText = createControl("STATIC", "Подготовка…", wsChild|ssLeft, sidebarW+34, 326, 500, 56, 0)
        ui.back = createControl("BUTTON", "Назад", wsChild|wsVisible|wsTabStop|bsPushButton, sidebarW+34, 448, 110, 38, idBack)
        ui.cancel = createControl("BUTTON", "Отмена", wsChild|wsVisible|wsTabStop|bsPushButton, sidebarW+300, 448, 110, 38, idCancel)
        ui.next = createControl("BUTTON", "Продолжить", wsChild|wsVisible|wsTabStop|bsPushButton, sidebarW+420, 448, 126, 38, idNext)

        setFont(ui.sidebarBrand, ui.titleFont)
        setFont(ui.sidebarVersion, ui.smallFont)
        setFont(ui.title, ui.titleFont)
        setFont(ui.desc, ui.bodyFont)
        setFont(ui.detail, ui.smallFont)
        for _, h := range []uintptr{ui.desktop, ui.removeData, ui.runAfter, ui.progressText, ui.back, ui.cancel, ui.next} { setFont(h, ui.smallFont) }
        pSendMessageW.Call(ui.progress, pbmSetRange32, 0, 100)
        pSendMessageW.Call(ui.desktop, 0x00F1, bstChecked, 0)
        pSendMessageW.Call(ui.runAfter, 0x00F1, bstChecked, 0)
        renderPage()
        return 0
    case wmCommand:
        id := int(wParam & 0xffff)
        code := int((wParam >> 16) & 0xffff)
        if code == bnClicked {
            switch id {
            case idNext: onNext()
            case idBack: onBack()
            case idCancel:
                if !ui.working { pDestroyWindow.Call(hwnd) }
            }
        }
        return 0
    case msgProgress:
        setProgress(int(wParam))
        ui.statusMu.Lock(); status := ui.status; ui.statusMu.Unlock()
        setText(ui.progressText, status)
        return 0
    case msgWorkDone:
        ui.working = false
        ui.failed = wParam != 0
        ui.page = pageFinish
        renderPage()
        if ui.uninstall && !ui.failed {
            scheduleSelfDelete()
        }
        return 0
    case wmCtlColorStatic:
        hdc := wParam
        pSetBkMode.Call(hdc, transparent)
        target := lParam
        if target == ui.sidebarBrand || target == ui.sidebarVersion {
            pSetTextColor.Call(hdc, rgb(255,255,255))
            brush, _, _ := pCreateSolidBrush.Call(rgb(36,103,235))
            return brush
        }
        pSetTextColor.Call(hdc, rgb(24,35,52))
        brush, _, _ := pCreateSolidBrush.Call(rgb(248,250,253))
        return brush
    case wmPaint:
        var ps paintStruct
        hdc, _, _ := pBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
        var r rect
        pGetClientRect.Call(hwnd, uintptr(unsafe.Pointer(&r)))
        bg, _, _ := pCreateSolidBrush.Call(rgb(248,250,253))
        pFillRect.Call(hdc, uintptr(unsafe.Pointer(&r)), bg)
        side := rect{Left:0, Top:0, Right:sidebarW, Bottom:r.Bottom}
        blue, _, _ := pCreateSolidBrush.Call(rgb(36,103,235))
        pFillRect.Call(hdc, uintptr(unsafe.Pointer(&side)), blue)
        pEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
        return 0
    case wmClose:
        if !ui.working { pDestroyWindow.Call(hwnd) }
        return 0
    case wmDestroy:
        pPostQuitMessage.Call(0)
        return 0
    }
    ret, _, _ := pDefWindowProcW.Call(hwnd, uintptr(message), wParam, lParam)
    return ret
}

func runQuietUninstall() int {
    ok, err := ensureElevated()
    if err != nil { return 2 }
    if !ok { return 0 }
    if err := uninstallProduct(false, func(int,string){}); err != nil { return 3 }
    scheduleSelfDelete()
    return 0
}

func main() {
    runtime.LockOSThread()
    if buildMode == "uninstaller" && len(os.Args) > 1 && strings.EqualFold(os.Args[1], "--quiet") {
        os.Exit(runQuietUninstall())
    }
    elevated, err := ensureElevated()
    if err != nil {
        syscall.MessageBox(0, wstr(err.Error()), wstr(productName), 0x10)
        return
    }
    if !elevated { return }

    ui.uninstall = buildMode == "uninstaller"
    ui.page = pageWelcome

    hinst, _, _ := pGetModuleHandleW.Call(0)
    className := wstr("LinkVideoHelperSetupWindow")
    wc := wndClassEx{
        CbSize: uint32(unsafe.Sizeof(wndClassEx{})),
        LpfnWndProc: syscall.NewCallback(wndProc),
        HInstance: hinst,
        HCursor: 0,
        HbrBackground: colorWindow + 1,
        LpszClassName: className,
    }
    if atom, _, _ := pRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc))); atom == 0 {
        return
    }
    title := productName + " — установка"
    if ui.uninstall { title = productName + " — удаление" }
    hwnd, _, _ := pCreateWindowExW.Call(
        0,
        uintptr(unsafe.Pointer(className)),
        uintptr(unsafe.Pointer(wstr(title))),
        wsOverlapped|wsCaption|wsSysMenu|wsMinimizeBox,
        0x80000000, 0x80000000, windowW, windowH,
        0, 0, hinst, 0,
    )
    if hwnd == 0 { return }
    ui.hwnd = hwnd
    pShowWindow.Call(hwnd, swShowNormal)
    pUpdateWindow.Call(hwnd)

    var m msg
    for {
        r, _, _ := pGetMessageW.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
        if int32(r) <= 0 { break }
        pTranslateMessage.Call(uintptr(unsafe.Pointer(&m)))
        pDispatchMessageW.Call(uintptr(unsafe.Pointer(&m)))
    }
}
