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
    clientWidth  = 900
    clientHeight = 580
    leftWidth    = 266

    pageWelcome = iota
    pageOptions
    pageProgress
    pageFinish

    idBack       = 1001
    idNext       = 1002
    idCancel     = 1003
    idDesktop    = 1010
    idRemoveData = 1011
    idRunAfter   = 1012
    idInstallPath = 1013
    idProgress   = 1020
    idProgressText = 1021

    wmCreate         = 0x0001
    wmDestroy        = 0x0002
    wmClose          = 0x0010
    wmPaint          = 0x000F
    wmEraseBkgnd     = 0x0014
    wmCommand        = 0x0111
    wmDrawItem       = 0x002B
    wmCtlColorStatic = 0x0138
    wmCtlColorBtn    = 0x0135
    wmCtlColorEdit   = 0x0133
    wmSetFont        = 0x0030
    wmSetRedraw      = 0x000B
    wmApp            = 0x8000
    msgProgress      = wmApp + 1
    msgWorkDone      = wmApp + 2
    msgStartWork     = wmApp + 3

    wsCaption      = 0x00C00000
    wsSysMenu      = 0x00080000
    wsMinimizeBox  = 0x00020000
    wsClipChildren = 0x02000000
    wsChild        = 0x40000000
    wsVisible      = 0x10000000
    wsTabStop      = 0x00010000
    wsBorder       = 0x00800000

    ssLeft         = 0x00000000
    bsOwnerDraw    = 0x0000000B
    bsAutoCheckbox = 0x00000003
    esReadOnly     = 0x0800

    swHide       = 0
    swShow       = 5
    bnClicked    = 0
    bstChecked   = 1
    bmGetCheck   = 0x00F0
    bmSetCheck   = 0x00F1
    odsDisabled  = 0x0004

    transparent  = 1
    psSolid      = 0
    dtLeft       = 0x0000
    dtCenter     = 0x0001
    dtVCenter    = 0x0004
    dtWordBreak  = 0x0010
    dtSingleLine = 0x0020

    pbmSetPos      = 0x0402
    pbmSetRange32  = 0x0406
    pbmSetBarColor = 0x0409
    pbmSetBkColor  = 0x2001
    pbsSmooth      = 0x01

    colorWindow    = 5
    idcArrow       = 32512
    idiApplication = 32512
    imageIcon      = 1
    iconSmall      = 0
    iconBig        = 1
)

type point struct{ X, Y int32 }
type rect struct{ Left, Top, Right, Bottom int32 }
type msg struct {
    Hwnd    uintptr
    Message uint32
    WParam  uintptr
    LParam  uintptr
    Time    uint32
    Pt      point
    Private uint32
}
type paintStruct struct {
    Hdc       uintptr
    Erase     int32
    RcPaint   rect
    Restore   int32
    IncUpdate int32
    Reserved  [32]byte
}
type wndClassEx struct {
    CbSize        uint32
    Style         uint32
    LpfnWndProc   uintptr
    CbClsExtra    int32
    CbWndExtra    int32
    HInstance     uintptr
    HIcon         uintptr
    HCursor       uintptr
    HbrBackground uintptr
    LpszMenuName  *uint16
    LpszClassName *uint16
    HIconSm       uintptr
}
type drawItemStruct struct {
    CtlType    uint32
    CtlID      uint32
    ItemID     uint32
    ItemAction uint32
    ItemState  uint32
    HwndItem   uintptr
    Hdc        uintptr
    RcItem     rect
    ItemData   uintptr
}
type initCommonControlsEx struct {
    DwSize uint32
    DwICC  uint32
}

type wizard struct {
    hwnd      uintptr
    page      int
    uninstall bool
    upgrade   bool
    working   bool
    failed    bool
    success   bool
    appPath   string

    pages map[int][]uintptr

    title       uintptr
    description uintptr
    installPath uintptr
    desktop     uintptr
    removeData  uintptr
    runAfter    uintptr
    finishInfo  uintptr
    progress    uintptr
    progressText uintptr
    back        uintptr
    next        uintptr
    cancel      uintptr

    fontNormal uintptr
    fontSmall  uintptr
    fontTitle  uintptr
    fontBold   uintptr
    fontBrand  uintptr
    fontHero   uintptr
    whiteBrush uintptr
    darkBrush  uintptr

    mu         sync.Mutex
    workStatus string
    workErr    error
    workPercent int
}

var current *wizard

var (
    user32   = syscall.NewLazyDLL("user32.dll")
    kernel32 = syscall.NewLazyDLL("kernel32.dll")
    gdi32    = syscall.NewLazyDLL("gdi32.dll")
    comctl32 = syscall.NewLazyDLL("comctl32.dll")

    pRegisterClassExW   = user32.NewProc("RegisterClassExW")
    pCreateWindowExW    = user32.NewProc("CreateWindowExW")
    pDefWindowProcW     = user32.NewProc("DefWindowProcW")
    pShowWindow         = user32.NewProc("ShowWindow")
    pUpdateWindow       = user32.NewProc("UpdateWindow")
    pGetMessageW        = user32.NewProc("GetMessageW")
    pTranslateMessage   = user32.NewProc("TranslateMessage")
    pDispatchMessageW   = user32.NewProc("DispatchMessageW")
    pPostQuitMessage    = user32.NewProc("PostQuitMessage")
    pDestroyWindow      = user32.NewProc("DestroyWindow")
    pPostMessageW       = user32.NewProc("PostMessageW")
    pSendMessageW       = user32.NewProc("SendMessageW")
    pEnableWindow       = user32.NewProc("EnableWindow")
    pSetWindowTextW     = user32.NewProc("SetWindowTextW")
    pGetWindowTextW     = user32.NewProc("GetWindowTextW")
    pBeginPaint         = user32.NewProc("BeginPaint")
    pEndPaint           = user32.NewProc("EndPaint")
    pFillRect           = user32.NewProc("FillRect")
    pInvalidateRect     = user32.NewProc("InvalidateRect")
    pRedrawWindow       = user32.NewProc("RedrawWindow")
    pGetClientRect      = user32.NewProc("GetClientRect")
    pGetSystemMetrics   = user32.NewProc("GetSystemMetrics")
    pAdjustWindowRectEx = user32.NewProc("AdjustWindowRectEx")
    pLoadCursorW        = user32.NewProc("LoadCursorW")
    pLoadIconW          = user32.NewProc("LoadIconW")
    pMessageBoxW        = user32.NewProc("MessageBoxW")
    pSetProcessDPIAware = user32.NewProc("SetProcessDPIAware")
    pDrawTextW          = user32.NewProc("DrawTextW")

    pGetModuleHandleW = kernel32.NewProc("GetModuleHandleW")

    pCreateSolidBrush = gdi32.NewProc("CreateSolidBrush")
    pDeleteObject     = gdi32.NewProc("DeleteObject")
    pCreateFontW      = gdi32.NewProc("CreateFontW")
    pSelectObject     = gdi32.NewProc("SelectObject")
    pSetBkMode        = gdi32.NewProc("SetBkMode")
    pSetTextColor     = gdi32.NewProc("SetTextColor")
    pCreatePen        = gdi32.NewProc("CreatePen")
    pRoundRect        = gdi32.NewProc("RoundRect")
    pPolygon          = gdi32.NewProc("Polygon")
    pPolyline         = gdi32.NewProc("Polyline")

    pInitCommonControlsEx = comctl32.NewProc("InitCommonControlsEx")
)

func main() {
    uninstall := strings.EqualFold(buildMode, "uninstaller")

    if uninstall {
        ready, err := ensureUninstallerRunsFromTemp()
        if err != nil {
            messageBox("Удаление LinkVideo.Helper", err.Error(), 0x10)
            return
        }
        if !ready {
            return
        }
    }

    elevated, err := ensureElevated()
    if err != nil {
        messageBox("LinkVideo.Helper", err.Error(), 0x10)
        return
    }
    if !elevated {
        return
    }

    if uninstall && hasArg("--quiet") {
        _ = uninstallProduct(false, func(int, string) {})
        scheduleSelfDelete()
        return
    }

    runWizard(uninstall)
}

func hasArg(wanted string) bool {
    for _, arg := range os.Args[1:] {
        if strings.EqualFold(strings.TrimSpace(arg), wanted) {
            return true
        }
    }
    return false
}

func existingInstallation() bool {
    _, err := os.Stat(defaultInstallDir() + `\` + appExeName)
    return err == nil
}

func runWizard(uninstall bool) {
    runtime.LockOSThread()
    defer runtime.UnlockOSThread()

    pSetProcessDPIAware.Call()
    init := initCommonControlsEx{DwSize: uint32(unsafe.Sizeof(initCommonControlsEx{})), DwICC: 0x00000020}
    pInitCommonControlsEx.Call(uintptr(unsafe.Pointer(&init)))

    w := &wizard{
        uninstall: uninstall,
        upgrade:   !uninstall && existingInstallation(),
        pages:     make(map[int][]uintptr),
    }
    current = w

    instance, _, _ := pGetModuleHandleW.Call(0)
    className := wstr("LinkVideoHelperInstallerWindow")
    cursor, _, _ := pLoadCursorW.Call(0, idcArrow)
    icon, _, _ := pLoadIconW.Call(instance, 1)
    if icon == 0 {
        icon, _, _ = pLoadIconW.Call(0, idiApplication)
    }
    wc := wndClassEx{
        CbSize:        uint32(unsafe.Sizeof(wndClassEx{})),
        Style:         0x0003,
        LpfnWndProc:   syscall.NewCallback(windowProc),
        HInstance:     instance,
        HIcon:         icon,
        HCursor:       cursor,
        HbrBackground: uintptr(colorWindow + 1),
        LpszClassName: className,
        HIconSm:       icon,
    }
    pRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc)))

    style := uintptr(wsCaption | wsSysMenu | wsMinimizeBox | wsClipChildren)
    rc := rect{0, 0, clientWidth, clientHeight}
    pAdjustWindowRectEx.Call(uintptr(unsafe.Pointer(&rc)), style, 0, 0)
    width := int(rc.Right - rc.Left)
    height := int(rc.Bottom - rc.Top)
    screenW, _, _ := pGetSystemMetrics.Call(0)
    screenH, _, _ := pGetSystemMetrics.Call(1)
    x := (int(screenW) - width) / 2
    y := (int(screenH) - height) / 2

    caption := "Установка LinkVideo.Helper"
    if uninstall {
        caption = "Удаление LinkVideo.Helper"
    }
    hwnd, _, callErr := pCreateWindowExW.Call(
        0,
        uintptr(unsafe.Pointer(className)),
        uintptr(unsafe.Pointer(wstr(caption))),
        style,
        uintptr(x), uintptr(y), uintptr(width), uintptr(height),
        0, 0, instance, 0,
    )
    if hwnd == 0 {
        messageBox(caption, "Не удалось открыть окно мастера: "+callErr.Error(), 0x10)
        return
    }
    w.hwnd = hwnd
    pShowWindow.Call(hwnd, swShow)
    pUpdateWindow.Call(hwnd)

    var m msg
    for {
        ret, _, _ := pGetMessageW.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
        if int32(ret) <= 0 {
            break
        }
        pTranslateMessage.Call(uintptr(unsafe.Pointer(&m)))
        pDispatchMessageW.Call(uintptr(unsafe.Pointer(&m)))
    }
}

func windowProc(hwnd uintptr, message uint32, wParam, lParam uintptr) uintptr {
    w := current
    switch message {
    case wmCreate:
        if w != nil {
            w.hwnd = hwnd
            w.createControls()
        }
        return 0
    case wmCommand:
        if w != nil {
            id := int(wParam & 0xFFFF)
            code := int((wParam >> 16) & 0xFFFF)
            if code == bnClicked || id == idBack || id == idNext || id == idCancel {
                w.handleCommand(id)
            }
        }
        return 0
    case wmDrawItem:
        if w != nil {
            return w.drawButton((*drawItemStruct)(unsafe.Pointer(lParam)))
        }
    case wmCtlColorStatic, wmCtlColorBtn, wmCtlColorEdit:
        if w != nil {
            pSetBkMode.Call(wParam, transparent)
            pSetTextColor.Call(wParam, rgb(32, 37, 43))
            return w.whiteBrush
        }
    case wmEraseBkgnd:
        return 1
    case wmPaint:
        if w != nil {
            w.paint()
            return 0
        }
    case msgProgress:
        if w != nil {
            w.applyProgress()
        }
        return 0
    case msgStartWork:
        if w != nil {
            w.startWork()
        }
        return 0
    case msgWorkDone:
        if w != nil {
            w.finishWork(wParam != 0)
        }
        return 0
    case wmClose:
        if w != nil && w.working {
            messageBox("LinkVideo.Helper", "Дождитесь завершения текущей операции.", 0x40)
            return 0
        }
        pDestroyWindow.Call(hwnd)
        return 0
    case wmDestroy:
        if w != nil {
            w.cleanup()
            if w.uninstall && w.success {
                scheduleSelfDelete()
            }
        }
        pPostQuitMessage.Call(0)
        return 0
    }
    result, _, _ := pDefWindowProcW.Call(hwnd, uintptr(message), wParam, lParam)
    return result
}

func (w *wizard) createControls() {
    w.whiteBrush, _, _ = pCreateSolidBrush.Call(rgb(255, 255, 255))
    w.darkBrush, _, _ = pCreateSolidBrush.Call(rgb(65, 67, 70))
    w.fontNormal = createFont(16, 400)
    w.fontSmall = createFont(14, 400)
    w.fontTitle = createFont(28, 700)
    w.fontBold = createFont(16, 700)
    w.fontBrand = createFont(25, 700)
    w.fontHero = createFont(23, 700)

    w.title = w.newControl("STATIC", "", wsChild|wsVisible|ssLeft, 0, 310, 45, 535, 44, 0)
    w.description = w.newControl("STATIC", "", wsChild|wsVisible|ssLeft, 0, 310, 92, 525, 58, 0)
    setFont(w.title, w.fontTitle)
    setFont(w.description, w.fontNormal)

    w.back = w.newControl("BUTTON", "Назад", wsChild|wsVisible|wsTabStop|bsOwnerDraw, 0, 523, 524, 108, 38, idBack)
    w.cancel = w.newControl("BUTTON", "Отмена", wsChild|wsVisible|wsTabStop|bsOwnerDraw, 0, 642, 524, 108, 38, idCancel)
    w.next = w.newControl("BUTTON", "Далее", wsChild|wsVisible|wsTabStop|bsOwnerDraw, 0, 761, 524, 108, 38, idNext)
    setFont(w.back, w.fontBold)
    setFont(w.cancel, w.fontBold)
    setFont(w.next, w.fontBold)

    if w.uninstall {
        w.createUninstallPages()
    } else {
        w.createInstallPages()
    }
    w.setPage(pageWelcome)
}

func (w *wizard) createInstallPages() {
    w.pages[pageWelcome] = []uintptr{}

    pathLabel := w.newControl("STATIC", "Папка установки", wsChild|ssLeft, 0, 310, 168, 300, 25, 0)
    w.installPath = w.newControl("EDIT", defaultInstallDir(), wsChild|wsBorder|esReadOnly, 0, 310, 197, 545, 38, idInstallPath)
    w.desktop = w.newControl("BUTTON", "Создать ярлык на рабочем столе", wsChild|wsTabStop|bsAutoCheckbox, 0, 310, 270, 520, 30, idDesktop)
    hint := w.newControl("STATIC", "При обновлении настройки, сохранённые данные входа и локальные параметры Helper не перезаписываются.", wsChild|ssLeft, 0, 310, 326, 535, 70, 0)
    setFont(pathLabel, w.fontBold)
    setFont(w.installPath, w.fontNormal)
    setFont(w.desktop, w.fontNormal)
    setFont(hint, w.fontSmall)
    setChecked(w.desktop, true)
    w.pages[pageOptions] = []uintptr{pathLabel, w.installPath, w.desktop, hint}

    w.progress = w.newControl("msctls_progress32", "", wsChild|pbsSmooth, 0, 310, 222, 545, 22, idProgress)
    w.progressText = w.newControl("STATIC", "Подготовка…", wsChild|ssLeft, 0, 310, 263, 545, 48, idProgressText)
    setFont(w.progressText, w.fontNormal)
    pSendMessageW.Call(w.progress, pbmSetRange32, 0, 100)
    pSendMessageW.Call(w.progress, pbmSetBarColor, 0, rgb(255, 173, 25))
    pSendMessageW.Call(w.progress, pbmSetBkColor, 0, rgb(238, 240, 242))
    w.pages[pageProgress] = []uintptr{w.progress, w.progressText}

    w.finishInfo = w.newControl("STATIC", "LinkVideo.Helper установлен и зарегистрирован в Windows. Удалить программу можно через «Установленные приложения» отдельным пошаговым мастером.", wsChild|ssLeft, 0, 310, 180, 535, 84, 0)
    w.runAfter = w.newControl("BUTTON", "Запустить LinkVideo.Helper", wsChild|wsTabStop|bsAutoCheckbox, 0, 310, 310, 500, 30, idRunAfter)
    setFont(w.finishInfo, w.fontNormal)
    setFont(w.runAfter, w.fontNormal)
    setChecked(w.runAfter, true)
    w.pages[pageFinish] = []uintptr{w.finishInfo, w.runAfter}
}

func (w *wizard) createUninstallPages() {
    confirm := w.newControl("STATIC", "LinkVideo.Helper и ярлыки будут удалены с этого компьютера.", wsChild|ssLeft, 0, 310, 178, 535, 64, 0)
    w.removeData = w.newControl("BUTTON", "Удалить также настройки, B2O-сессию и локальный кэш", wsChild|wsTabStop|bsAutoCheckbox, 0, 310, 268, 545, 44, idRemoveData)
    hint := w.newControl("STATIC", "По умолчанию настройки сохраняются, чтобы после повторной установки Helper продолжил работу с прежними параметрами. Выгруженные видеозаписи не удаляются.", wsChild|ssLeft, 0, 338, 326, 505, 78, 0)
    setFont(confirm, w.fontNormal)
    setFont(w.removeData, w.fontNormal)
    setFont(hint, w.fontSmall)
    setChecked(w.removeData, false)
    w.pages[pageWelcome] = []uintptr{confirm, w.removeData, hint}

    w.progress = w.newControl("msctls_progress32", "", wsChild|pbsSmooth, 0, 310, 222, 545, 22, idProgress)
    w.progressText = w.newControl("STATIC", "Подготовка…", wsChild|ssLeft, 0, 310, 263, 545, 48, idProgressText)
    setFont(w.progressText, w.fontNormal)
    pSendMessageW.Call(w.progress, pbmSetRange32, 0, 100)
    pSendMessageW.Call(w.progress, pbmSetBarColor, 0, rgb(255, 173, 25))
    pSendMessageW.Call(w.progress, pbmSetBkColor, 0, rgb(238, 240, 242))
    w.pages[pageProgress] = []uintptr{w.progress, w.progressText}

    w.finishInfo = w.newControl("STATIC", "LinkVideo.Helper удалён. Окно можно закрыть.", wsChild|ssLeft, 0, 310, 188, 535, 60, 0)
    setFont(w.finishInfo, w.fontNormal)
    w.pages[pageFinish] = []uintptr{w.finishInfo}
}

func (w *wizard) setPage(page int) {
    pSendMessageW.Call(w.hwnd, wmSetRedraw, 0, 0)
    defer func() {
        pSendMessageW.Call(w.hwnd, wmSetRedraw, 1, 0)
        pRedrawWindow.Call(w.hwnd, 0, 0, 0x0001|0x0004|0x0080|0x0100)
    }()

    for _, controls := range w.pages {
        for _, hwnd := range controls {
            pShowWindow.Call(hwnd, swHide)
        }
    }
    w.page = page
    for _, hwnd := range w.pages[page] {
        pShowWindow.Call(hwnd, swShow)
    }

    if !w.uninstall {
        switch page {
        case pageWelcome:
            if w.upgrade {
                setText(w.title, "Обновление LinkVideo.Helper")
                setText(w.description, "Обновление рабочих инструментов LinkVideo без потери пользовательских настроек.")
            } else {
                setText(w.title, "LinkVideo.Helper")
                setText(w.description, "Единый рабочий инструмент для VPN, архивов и диагностики LinkVideo.")
            }
            setText(w.next, "Далее")
            show(w.back, false); show(w.cancel, true); show(w.next, true)
        case pageOptions:
            setText(w.title, "Параметры установки")
            setText(w.description, "Проверьте расположение программы и создание ярлыка.")
            setText(w.next, "Установить")
            show(w.back, true); show(w.cancel, true); show(w.next, true)
        case pageProgress:
            title := "Установка LinkVideo.Helper"
            if w.upgrade { title = "Обновление LinkVideo.Helper" }
            setText(w.title, title)
            setText(w.description, "Не закрывайте окно до завершения процесса.")
            show(w.back, false); show(w.cancel, false); show(w.next, false)
        case pageFinish:
            if w.failed {
                setText(w.title, "Установка не завершена")
                w.mu.Lock(); detail := w.workStatus; w.mu.Unlock()
                setText(w.description, detail)
                setText(w.finishInfo, "Установка остановлена. Предыдущие пользовательские настройки не удалялись.")
                show(w.runAfter, false)
            } else {
                setText(w.title, "Готово")
                setText(w.description, fmt.Sprintf("LinkVideo.Helper %s успешно установлен.", version))
                show(w.runAfter, true)
            }
            setText(w.next, "Готово")
            show(w.back, false); show(w.cancel, false); show(w.next, true)
        }
    } else {
        switch page {
        case pageWelcome:
            setText(w.title, "Удаление LinkVideo.Helper")
            setText(w.description, "Подтвердите удаление программы с этого компьютера.")
            setText(w.next, "Удалить")
            show(w.back, false); show(w.cancel, true); show(w.next, true)
        case pageProgress:
            setText(w.title, "Удаление LinkVideo.Helper")
            setText(w.description, "Не закрывайте окно до завершения процесса.")
            show(w.back, false); show(w.cancel, false); show(w.next, false)
        case pageFinish:
            if w.failed {
                setText(w.title, "Удаление завершено с ошибкой")
                w.mu.Lock(); detail := w.workStatus; w.mu.Unlock()
                setText(w.description, detail)
            } else {
                setText(w.title, "Удаление завершено")
                setText(w.description, "LinkVideo.Helper удалён с компьютера.")
            }
            setText(w.next, "Готово")
            show(w.back, false); show(w.cancel, false); show(w.next, true)
        }
    }
    pInvalidateRect.Call(w.hwnd, 0, 1)
}

func (w *wizard) handleCommand(id int) {
    if w.working { return }
    switch id {
    case idBack:
        if !w.uninstall && w.page == pageOptions {
            w.setPage(pageWelcome)
        }
    case idCancel:
        pDestroyWindow.Call(w.hwnd)
    case idNext:
        if !w.uninstall {
            switch w.page {
            case pageWelcome:
                w.setPage(pageOptions)
            case pageOptions:
                w.setPage(pageProgress)
                pPostMessageW.Call(w.hwnd, msgStartWork, 0, 0)
            case pageFinish:
                if !w.failed && isChecked(w.runAfter) && w.appPath != "" {
                    _ = launchApplication(w.appPath)
                }
                pDestroyWindow.Call(w.hwnd)
            }
        } else {
            switch w.page {
            case pageWelcome:
                w.setPage(pageProgress)
                pPostMessageW.Call(w.hwnd, msgStartWork, 0, 0)
            case pageFinish:
                pDestroyWindow.Call(w.hwnd)
            }
        }
    }
}

func (w *wizard) startWork() {
    if w.working { return }
    w.working = true
    desktop := isChecked(w.desktop)
    removeData := isChecked(w.removeData)

    go func() {
        progress := func(percent int, status string) {
            w.mu.Lock()
            w.workPercent = percent
            w.workStatus = status
            w.mu.Unlock()
            pPostMessageW.Call(w.hwnd, msgProgress, 0, 0)
        }
        var err error
        if w.uninstall {
            err = uninstallProduct(removeData, progress)
        } else {
            w.appPath, err = installProduct(installOptions{DesktopShortcut: desktop}, progress)
        }
        w.mu.Lock()
        w.workErr = err
        if err != nil { w.workStatus = err.Error() }
        w.mu.Unlock()
        fail := uintptr(0)
        if err != nil { fail = 1 }
        pPostMessageW.Call(w.hwnd, msgWorkDone, fail, 0)
    }()
}

func (w *wizard) applyProgress() {
    w.mu.Lock()
    percent := w.workPercent
    status := w.workStatus
    w.mu.Unlock()
    if percent < 0 { percent = 0 }
    if percent > 100 { percent = 100 }
    pSendMessageW.Call(w.progress, pbmSetPos, uintptr(percent), 0)
    setText(w.progressText, status)
}

func (w *wizard) finishWork(failed bool) {
    w.working = false
    w.failed = failed
    w.success = !failed
    w.applyProgress()
    w.setPage(pageFinish)
}

func (w *wizard) newControl(class, text string, style, exStyle uintptr, x, y, width, height, id int) uintptr {
    instance, _, _ := pGetModuleHandleW.Call(0)
    hwnd, _, _ := pCreateWindowExW.Call(
        exStyle,
        uintptr(unsafe.Pointer(wstr(class))),
        uintptr(unsafe.Pointer(wstr(text))),
        style,
        uintptr(x), uintptr(y), uintptr(width), uintptr(height),
        w.hwnd, uintptr(id), instance, 0,
    )
    if id != 0 { setFont(hwnd, w.fontNormal) }
    return hwnd
}

func (w *wizard) paint() {
    var ps paintStruct
    hdc, _, _ := pBeginPaint.Call(w.hwnd, uintptr(unsafe.Pointer(&ps)))
    defer pEndPaint.Call(w.hwnd, uintptr(unsafe.Pointer(&ps)))

    var client rect
    pGetClientRect.Call(w.hwnd, uintptr(unsafe.Pointer(&client)))
    right := rect{leftWidth, 0, client.Right, client.Bottom}
    left := rect{0, 0, leftWidth, client.Bottom}
    pFillRect.Call(hdc, uintptr(unsafe.Pointer(&right)), w.whiteBrush)
    pFillRect.Call(hdc, uintptr(unsafe.Pointer(&left)), w.darkBrush)

    // Same LinkVideo chain mark and sidebar language as LinkVideo.Monitor.
    orangeBrush, _, _ := pCreateSolidBrush.Call(rgb(255, 173, 25))
    greyBrush, _, _ := pCreateSolidBrush.Call(rgb(201, 204, 207))
    oldBrush, _, _ := pSelectObject.Call(hdc, orangeBrush)
    p1 := [4]point{{34, 52}, {49, 37}, {64, 52}, {49, 67}}
    pPolygon.Call(hdc, uintptr(unsafe.Pointer(&p1[0])), 4)
    pSelectObject.Call(hdc, greyBrush)
    p2 := [4]point{{55, 40}, {66, 29}, {77, 40}, {66, 51}}
    pPolygon.Call(hdc, uintptr(unsafe.Pointer(&p2[0])), 4)
    pSelectObject.Call(hdc, oldBrush)
    pDeleteObject.Call(orangeBrush)
    pDeleteObject.Call(greyBrush)

    drawText(hdc, "LinkVideo", rect{89, 27, 235, 59}, w.fontBrand, rgb(255, 255, 255), dtLeft|dtVCenter|dtSingleLine)
    drawText(hdc, "HELPER", rect{90, 57, 230, 81}, w.fontSmall, rgb(255, 173, 25), dtLeft|dtVCenter|dtSingleLine)
    drawText(hdc, "Рабочие", rect{34, 122, 235, 157}, w.fontHero, rgb(255, 255, 255), dtLeft|dtVCenter|dtSingleLine)
    drawText(hdc, "инструменты", rect{34, 154, 235, 189}, w.fontHero, rgb(255, 255, 255), dtLeft|dtVCenter|dtSingleLine)

    steps := w.stepLabels()
    active := w.activeStep()
    y := int32(255)
    for i, label := range steps {
        color := rgb(174, 179, 184)
        bullet := rgb(113, 118, 123)
        if i == active {
            color = rgb(255, 255, 255)
            bullet = rgb(255, 173, 25)
        } else if i < active {
            color = rgb(220, 223, 226)
            bullet = rgb(49, 198, 109)
        }
        b, _, _ := pCreateSolidBrush.Call(bullet)
        dot := rect{35, y + 7, 44, y + 16}
        pFillRect.Call(hdc, uintptr(unsafe.Pointer(&dot)), b)
        pDeleteObject.Call(b)
        drawText(hdc, label, rect{58, y, 225, y + 28}, w.fontNormal, color, dtLeft|dtVCenter|dtSingleLine)
        y += 48
    }

    drawText(hdc, "Версия "+version, rect{34, client.Bottom - 66, 225, client.Bottom - 38}, w.fontSmall, rgb(174, 179, 184), dtLeft|dtVCenter|dtSingleLine)
    drawText(hdc, "linkvideo.ru", rect{34, client.Bottom - 39, 225, client.Bottom - 15}, w.fontSmall, rgb(255, 173, 25), dtLeft|dtVCenter|dtSingleLine)

    if !w.uninstall && w.page == pageWelcome {
        w.drawWelcome(hdc)
    }

    lineBrush, _, _ := pCreateSolidBrush.Call(rgb(223, 227, 232))
    line := rect{leftWidth, 506, client.Right, 507}
    pFillRect.Call(hdc, uintptr(unsafe.Pointer(&line)), lineBrush)
    pDeleteObject.Call(lineBrush)
}

func (w *wizard) drawWelcome(hdc uintptr) {
    accentBrush, _, _ := pCreateSolidBrush.Call(rgb(255, 173, 25))
    accent := rect{310, 151, 398, 155}
    pFillRect.Call(hdc, uintptr(unsafe.Pointer(&accent)), accentBrush)
    pDeleteObject.Call(accentBrush)

    cards := []struct{ title, body string }{
        {"VPN-клиенты", "Создание, поиск и управление L2TP-клиентами."},
        {"Архив", "Поиск, выгрузка и диагностика записей камер."},
        {"Инфраструктура", "VPN-серверы, резервные копии и LV Automation."},
    }
    baseX := int32(310)
    const cardW int32 = 168
    const gap int32 = 14
    for i, card := range cards {
        x := baseX + int32(i)*(cardW+gap)
        w.drawBenefitCard(hdc, rect{x, 164, x + cardW, 312}, card.title, card.body)
    }
    w.drawRequirementsCard(hdc, rect{310, 342, 842, 448})
}

func (w *wizard) drawBenefitCard(hdc uintptr, rc rect, title, body string) {
    brush, _, _ := pCreateSolidBrush.Call(rgb(248, 249, 250))
    pen, _, _ := pCreatePen.Call(psSolid, 1, rgb(226, 229, 233))
    oldBrush, _, _ := pSelectObject.Call(hdc, brush)
    oldPen, _, _ := pSelectObject.Call(hdc, pen)
    pRoundRect.Call(hdc, uintptr(rc.Left), uintptr(rc.Top), uintptr(rc.Right), uintptr(rc.Bottom), 16, 16)
    pSelectObject.Call(hdc, oldBrush)
    pSelectObject.Call(hdc, oldPen)
    pDeleteObject.Call(brush)
    pDeleteObject.Call(pen)

    checkPen, _, _ := pCreatePen.Call(psSolid, 5, rgb(255, 126, 61))
    oldCheckPen, _, _ := pSelectObject.Call(hdc, checkPen)
    points := [3]point{{rc.Left + 18, rc.Top + 29}, {rc.Left + 27, rc.Top + 38}, {rc.Left + 43, rc.Top + 20}}
    pPolyline.Call(hdc, uintptr(unsafe.Pointer(&points[0])), 3)
    pSelectObject.Call(hdc, oldCheckPen)
    pDeleteObject.Call(checkPen)

    drawText(hdc, title, rect{rc.Left + 16, rc.Top + 54, rc.Right - 14, rc.Top + 94}, w.fontBold, rgb(36, 40, 44), dtLeft|dtWordBreak)
    drawText(hdc, body, rect{rc.Left + 16, rc.Top + 96, rc.Right - 14, rc.Bottom - 12}, w.fontSmall, rgb(78, 83, 89), dtLeft|dtWordBreak)
}

func (w *wizard) drawRequirementsCard(hdc uintptr, rc rect) {
    brush, _, _ := pCreateSolidBrush.Call(rgb(255, 247, 230))
    pen, _, _ := pCreatePen.Call(psSolid, 1, rgb(255, 221, 155))
    oldBrush, _, _ := pSelectObject.Call(hdc, brush)
    oldPen, _, _ := pSelectObject.Call(hdc, pen)
    pRoundRect.Call(hdc, uintptr(rc.Left), uintptr(rc.Top), uintptr(rc.Right), uintptr(rc.Bottom), 14, 14)
    pSelectObject.Call(hdc, oldBrush)
    pSelectObject.Call(hdc, oldPen)
    pDeleteObject.Call(brush)
    pDeleteObject.Call(pen)

    drawText(hdc, "Системные требования", rect{rc.Left + 16, rc.Top + 12, rc.Right - 14, rc.Top + 39}, w.fontBold, rgb(44, 46, 49), dtLeft|dtVCenter|dtSingleLine)
    drawText(hdc, "Windows 10/11 · x64", rect{rc.Left + 16, rc.Top + 46, rc.Left + 205, rc.Top + 73}, w.fontSmall, rgb(69, 72, 76), dtLeft|dtVCenter|dtSingleLine)
    drawText(hdc, "Сеть до VPN/RouterOS", rect{rc.Left + 210, rc.Top + 46, rc.Left + 385, rc.Top + 73}, w.fontSmall, rgb(69, 72, 76), dtLeft|dtVCenter|dtSingleLine)
    drawText(hdc, "Права администратора", rect{rc.Left + 390, rc.Top + 46, rc.Right - 12, rc.Top + 73}, w.fontSmall, rgb(69, 72, 76), dtLeft|dtVCenter|dtSingleLine)
}

func (w *wizard) stepLabels() []string {
    if w.uninstall {
        return []string{"Подтверждение", "Удаление", "Готово"}
    }
    return []string{"Начало", "Параметры", "Установка", "Готово"}
}

func (w *wizard) activeStep() int {
    if w.uninstall {
        switch w.page {
        case pageWelcome: return 0
        case pageProgress: return 1
        default: return 2
        }
    }
    switch w.page {
    case pageWelcome: return 0
    case pageOptions: return 1
    case pageProgress: return 2
    default: return 3
    }
}

func (w *wizard) drawButton(dis *drawItemStruct) uintptr {
    if dis == nil { return 0 }
    primary := int(dis.CtlID) == idNext
    disabled := dis.ItemState&odsDisabled != 0
    fill := rgb(255, 255, 255)
    border := rgb(205, 211, 217)
    textColor := rgb(45, 50, 55)
    if primary {
        fill = rgb(255, 173, 25)
        border = rgb(235, 150, 0)
        textColor = rgb(43, 39, 31)
    }
    if disabled {
        fill = rgb(238, 240, 242)
        border = rgb(222, 225, 228)
        textColor = rgb(145, 150, 155)
    }
    brush, _, _ := pCreateSolidBrush.Call(fill)
    pen, _, _ := pCreatePen.Call(psSolid, 1, border)
    oldBrush, _, _ := pSelectObject.Call(dis.Hdc, brush)
    oldPen, _, _ := pSelectObject.Call(dis.Hdc, pen)
    pRoundRect.Call(dis.Hdc, uintptr(dis.RcItem.Left), uintptr(dis.RcItem.Top), uintptr(dis.RcItem.Right), uintptr(dis.RcItem.Bottom), 10, 10)
    pSelectObject.Call(dis.Hdc, oldBrush)
    pSelectObject.Call(dis.Hdc, oldPen)
    pDeleteObject.Call(brush)
    pDeleteObject.Call(pen)
    drawText(dis.Hdc, getText(dis.HwndItem), dis.RcItem, w.fontBold, textColor, dtCenter|dtVCenter|dtSingleLine)
    return 1
}

func (w *wizard) cleanup() {
    for _, object := range []uintptr{w.fontNormal, w.fontSmall, w.fontTitle, w.fontBold, w.fontBrand, w.fontHero, w.whiteBrush, w.darkBrush} {
        if object != 0 { pDeleteObject.Call(object) }
    }
}

func createFont(size int32, weight int32) uintptr {
    font, _, _ := pCreateFontW.Call(
        uintptr(int64(-size)), 0, 0, 0, uintptr(weight), 0, 0, 0,
        1, 0, 0, 5, 0, uintptr(unsafe.Pointer(wstr("Segoe UI"))),
    )
    return font
}

func setFont(hwnd, font uintptr) {
    if hwnd != 0 && font != 0 { pSendMessageW.Call(hwnd, wmSetFont, font, 1) }
}
func setText(hwnd uintptr, text string) {
    if hwnd != 0 { pSetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(wstr(text)))) }
}
func getText(hwnd uintptr) string {
    buf := make([]uint16, 256)
    n, _, _ := pGetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
    return syscall.UTF16ToString(buf[:n])
}
func setChecked(hwnd uintptr, checked bool) {
    if hwnd == 0 { return }
    state := uintptr(0)
    if checked { state = bstChecked }
    pSendMessageW.Call(hwnd, bmSetCheck, state, 0)
}
func isChecked(hwnd uintptr) bool {
    if hwnd == 0 { return false }
    state, _, _ := pSendMessageW.Call(hwnd, bmGetCheck, 0, 0)
    return state == bstChecked
}
func show(hwnd uintptr, visible bool) {
    if hwnd == 0 { return }
    cmd := uintptr(swHide)
    if visible { cmd = swShow }
    pShowWindow.Call(hwnd, cmd)
}
func enable(hwnd uintptr, enabled bool) {
    value := uintptr(0)
    if enabled { value = 1 }
    pEnableWindow.Call(hwnd, value)
    pInvalidateRect.Call(hwnd, 0, 1)
}
func drawText(hdc uintptr, text string, rc rect, font uintptr, color uintptr, flags uintptr) {
    oldFont, _, _ := pSelectObject.Call(hdc, font)
    pSetBkMode.Call(hdc, transparent)
    pSetTextColor.Call(hdc, color)
    p := wstr(text)
    pDrawTextW.Call(hdc, uintptr(unsafe.Pointer(p)), uintptr(^uint32(0)), uintptr(unsafe.Pointer(&rc)), flags)
    pSelectObject.Call(hdc, oldFont)
}
func wstr(s string) *uint16 {
    p, _ := syscall.UTF16PtrFromString(s)
    return p
}
func rgb(r, g, b byte) uintptr {
    return uintptr(r) | uintptr(g)<<8 | uintptr(b)<<16
}
func messageBox(title, text string, flags uintptr) {
    pMessageBoxW.Call(0, uintptr(unsafe.Pointer(wstr(text))), uintptr(unsafe.Pointer(wstr(title))), flags)
}
