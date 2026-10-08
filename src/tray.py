# tray.py

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

from PyQt6.QtCore import QPointF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QIcon, QImage, QPainter
from PyQt6.QtWidgets import QMessageBox

from ui.launch_control import mode_label_for_launch_method, normalize_launch_phase, phase_color
from ui.message_box_accessibility import set_message_box_button_accessibility
from ui.tray_menu.model import TrayMenuState, build_tray_menu, tray_status_text

try:
    from log.log import log

except Exception:
    def log(*args, **kwargs):  # type: ignore[no-redef]
        return None

try:
    from app.ui_texts import tr as tr_catalog
except Exception:
    def tr_catalog(_key, *, language=None, default=""):  # type: ignore[no-redef]
        return default

if sys.platform == "win32":
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    gdi32 = ctypes.windll.gdi32
    _PTR_IS_64 = ctypes.sizeof(ctypes.c_void_p) == 8
    WPARAM = ctypes.c_uint64 if _PTR_IS_64 else ctypes.c_uint
    LPARAM = ctypes.c_int64 if _PTR_IS_64 else ctypes.c_long
    LRESULT = getattr(
        ctypes,
        "c_ssize_t",
        ctypes.c_int64 if _PTR_IS_64 else ctypes.c_long,
    )

    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    LR_DEFAULTSIZE = 0x0040

    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002
    NIM_SETVERSION = 0x00000004

    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004
    NIF_INFO = 0x00000010
    NIF_SHOWTIP = 0x00000080

    NOTIFYICON_VERSION_4 = 4
    NIIF_NONE = 0x00000000
    NIIF_INFO = 0x00000001

    WM_APP = 0x8000
    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_CONTEXTMENU = 0x007B
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205
    WM_LBUTTONDBLCLK = 0x0203

    NIN_SELECT = WM_USER = 0x0400
    NIN_KEYSELECT = WM_USER + 1

    IDI_APPLICATION = 32512
    CW_USEDEFAULT = 0x80000000
    SM_CXSMICON = 49
    BI_RGB = 0
    DIB_RGB_COLORS = 0

    TRAY_CALLBACK_MESSAGE = WM_APP + 100


    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]


    class _NotifyIconTimeoutUnion(ctypes.Union):
        _fields_ = [
            ("uTimeout", wintypes.UINT),
            ("uVersion", wintypes.UINT),
        ]


    class NOTIFYICONDATAW(ctypes.Structure):
        _anonymous_ = ("timeout_version",)
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HANDLE),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("timeout_version", _NotifyIconTimeoutUnion),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", wintypes.HANDLE),
        ]


    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT,
        wintypes.HWND,
        wintypes.UINT,
        WPARAM,
        LPARAM,
    )


    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HCURSOR),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]


    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]


    class ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]


    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.LoadIconW.restype = wintypes.HICON
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
    user32.CreateIconIndirect.restype = wintypes.HICON
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(BITMAPINFOHEADER),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
    gdi32.CreateBitmap.restype = wintypes.HBITMAP
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL


def _make_int_resource(identifier: int):
    if sys.platform != "win32":
        return None
    return ctypes.cast(ctypes.c_void_p(identifier), wintypes.LPCWSTR)


def _truncate_text(value: str, max_length: int) -> str:
    return str(value or "")[: max(0, max_length - 1)]


def _loword(value: int) -> int:
    return int(value) & 0xFFFF


def _signed_word(value: int) -> int:
    value = int(value) & 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _get_x_lparam(value: int) -> int:
    return _signed_word(value)


def _get_y_lparam(value: int) -> int:
    return _signed_word(int(value) >> 16)


# ---- иконка трея с точкой состояния ----------------------------------------

# Точка в правом нижнем углу логотипа: примерно 40% стороны. Вокруг неё логотип
# «вырезается» прозрачным кольцом — так точка читается и на светлой, и на тёмной
# панели задач, как значки-наклейки самой Windows.
STATUS_DOT_RADIUS_RATIO = 0.2
STATUS_DOT_CUTOUT_RATIO = 0.075


def tray_icon_dot_color(phase: str) -> str | None:
    """Цвет точки на иконке трея. Когда Zapret остановлен, точки нет вовсе."""
    color = phase_color(phase)
    if normalize_launch_phase(phase) == "failed":
        # Ошибка видна в меню и уведомлениях; на иконке красная точка
        # «мозолила бы глаза» до следующего запуска.
        return None
    return color


def render_status_icon_image(base: QImage, color: str | None, size: int) -> QImage:
    """Рисует логотип размера size×size и, если задан цвет, точку состояния в углу."""
    size = max(8, int(size))
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if not base.isNull():
        scaled = base.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawImage(
            QPointF((size - scaled.width()) / 2, (size - scaled.height()) / 2),
            scaled,
        )
    if color:
        radius = size * STATUS_DOT_RADIUS_RATIO
        cutout = radius + max(1.0, size * STATUS_DOT_CUTOUT_RATIO)
        center = QPointF(size - radius - 0.5, size - radius - 0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.setBrush(Qt.GlobalColor.black)
        painter.drawEllipse(center, cutout, cutout)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setBrush(QColor(color))
        painter.drawEllipse(center, radius, radius)
    painter.end()
    return image


def _tray_icon_size() -> int:
    if sys.platform == "win32":
        try:
            size = int(user32.GetSystemMetrics(SM_CXSMICON))
            if size > 0:
                return size
        except Exception:
            pass
    return 16


def _hicon_from_image(image: QImage):
    """QImage → HICON: 32-битная картинка с альфа-каналом через CreateIconIndirect."""
    if sys.platform != "win32" or image.isNull():
        return None

    argb = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = argb.width(), argb.height()
    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # строки сверху вниз, как в QImage
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = BI_RGB

    bits = ctypes.c_void_p()
    hdc = user32.GetDC(None)
    try:
        color_bitmap = gdi32.CreateDIBSection(hdc, ctypes.byref(header), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
    finally:
        user32.ReleaseDC(None, hdc)
    if not color_bitmap or not bits.value:
        return None

    try:
        pixels = argb.constBits()
        pixels.setsize(argb.sizeInBytes())
        row_bytes = width * 4
        source = bytes(pixels)
        if argb.bytesPerLine() == row_bytes:
            ctypes.memmove(bits, source, row_bytes * height)
        else:
            for row in range(height):
                start = row * argb.bytesPerLine()
                ctypes.memmove(bits.value + row * row_bytes, source[start:start + row_bytes], row_bytes)

        # Монохромная маска из нулей: прозрачность задаёт альфа-канал цветной части.
        mask_stride = ((width + 15) // 16) * 2
        mask_bits = ctypes.create_string_buffer(mask_stride * height)
        mask_bitmap = gdi32.CreateBitmap(width, height, 1, 1, mask_bits)
        try:
            info = ICONINFO()
            info.fIcon = True
            info.hbmMask = mask_bitmap
            info.hbmColor = color_bitmap
            return user32.CreateIconIndirect(ctypes.byref(info)) or None
        finally:
            if mask_bitmap:
                gdi32.DeleteObject(mask_bitmap)
    finally:
        gdi32.DeleteObject(color_bitmap)


# ---- тексты ---------------------------------------------------------------

def build_tray_tooltip(*, phase: str, launch_method: str, preset_name: str, language: str | None = None) -> str:
    mode = mode_label_for_launch_method(launch_method)
    lines = [f"{mode} — {tray_status_text(phase, language=language)}"]
    if preset_name:
        lines.append(
            tr_catalog("tray.tooltip.preset", language=language, default="Пресет: {preset}").format(preset=preset_name)
        )
    return "\n".join(lines)


class SystemTrayManager:
    """Windows-first менеджер системного трея.

    Production-путь для Windows один: native tray icon через Shell_NotifyIcon
    без Qt-tray слоя. Меню — своё окошко ui.tray_menu: менеджер отдаёт ему
    описание строк и выполняет команду выбранной строки.
    """

    STOPPED_NOTIFY_DELAY_MS = 1500

    def __init__(self, window_port, icon_path, app_version, *, tray_feature):
        self.window_port = window_port
        self._tray_feature = tray_feature
        self.icon_path = os.path.abspath(icon_path)
        self.app_version = str(app_version or "").strip()
        self._icon_visible = False
        self._tray_hint_shown_this_session = False
        self._popup = None
        self._toggle_request_pending = False
        self._last_toggle_monotonic = 0.0
        self._icon_handle = None
        self._status_icon_handles: dict[str, object] = {}
        self._status_icon_key = ""
        self._base_icon_image: QImage | None = None
        self._tooltip = ""
        self._launch_phase = ""
        self._launch_method = ""
        self._preset_name = ""
        self._stopped_notify_timer = None
        self._message_window = None
        self._taskbar_created_message = None
        self._class_name = f"Zapret2TrayWindow_{os.getpid()}_{id(self):x}"

        if sys.platform != "win32":
            log("Native Windows tray backend недоступен вне Windows", "DEBUG")
            return

        self._create_native_backend()

    def _create_native_backend(self) -> None:
        self._icon_handle = self._load_icon_handle(self.icon_path)
        self._message_window = _TrayMessageWindow(self)
        self._taskbar_created_message = self._message_window.taskbar_created_message
        self._add_icon()
        self._connect_proxy_status_signal()

    def _load_icon_handle(self, icon_path: str):
        if sys.platform != "win32":
            return None

        handle = None
        if os.path.exists(icon_path):
            try:
                handle = user32.LoadImageW(
                    None,
                    icon_path,
                    IMAGE_ICON,
                    0,
                    0,
                    LR_LOADFROMFILE | LR_DEFAULTSIZE,
                )
            except Exception:
                handle = None

        if handle:
            return handle

        try:
            return user32.LoadIconW(None, _make_int_resource(IDI_APPLICATION))
        except Exception:
            return None

    # ---- native иконка --------------------------------------------------

    def _current_icon_handle(self):
        return self._status_icon_handles.get(self._status_icon_key) or self._icon_handle

    def _build_notify_icon_data(self, flags: int) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = TRAY_CALLBACK_MESSAGE
        data.hIcon = self._current_icon_handle()
        data.szTip = _truncate_text(self._tooltip or f"Zapret2 v{self.app_version}", 128)
        return data

    @property
    def _hwnd(self):
        window = self._message_window
        return 0 if window is None else int(window.hwnd or 0)

    def _add_icon(self) -> None:
        if sys.platform != "win32" or not self._hwnd:
            return

        data = self._build_notify_icon_data(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        if bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data))):
            data.uVersion = NOTIFYICON_VERSION_4
            shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(data))
            self._icon_visible = True
            log("Native tray icon added", "DEBUG")
        else:
            log("Не удалось добавить native tray icon", "WARNING")

    def _modify_icon(self) -> None:
        if sys.platform != "win32" or not self._hwnd or not self._icon_visible:
            return
        data = self._build_notify_icon_data(NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def recreate_icon(self) -> None:
        self.hide_icon()
        self._add_icon()

    def hide_icon(self) -> None:
        if sys.platform != "win32" or not self._hwnd or not self._icon_visible:
            return

        data = self._build_notify_icon_data(0)
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        self._icon_visible = False

    def cleanup(self) -> None:
        self._cancel_stopped_notification()
        self.hide_icon()
        popup, self._popup = self._popup, None
        if popup is not None:
            popup.hide()
            popup.deleteLater()

        window = self._message_window
        self._message_window = None
        if window is not None:
            window.destroy()
            if getattr(window, "_class_registered", False):
                self._message_window = window

        handles = [self._icon_handle, *self._status_icon_handles.values()]
        self._icon_handle = None
        self._status_icon_handles = {}
        self._status_icon_key = ""
        if sys.platform == "win32":
            for handle in handles:
                if not handle:
                    continue
                try:
                    user32.DestroyIcon(handle)
                except Exception:
                    pass

    def _status_icon_handle_for(self, color: str | None):
        if not color:
            return None
        cached = self._status_icon_handles.get(color)
        if cached:
            return cached
        if self._base_icon_image is None:
            self._base_icon_image = QIcon(self.icon_path).pixmap(QSize(64, 64)).toImage()
        image = render_status_icon_image(self._base_icon_image, color, _tray_icon_size())
        handle = _hicon_from_image(image)
        if handle:
            self._status_icon_handles[color] = handle
        return handle

    # ---- состояние запуска -------------------------------------------

    def apply_launch_status(self, *, phase: str, launch_method: str, preset_name: str) -> None:
        """Обновляет иконку, подсказку и уведомления по новой фазе запуска."""
        previous_phase = self._launch_phase
        phase = normalize_launch_phase(phase)
        self._launch_phase = phase
        self._launch_method = str(launch_method or "")
        self._preset_name = str(preset_name or "")

        color = tray_icon_dot_color(phase)
        icon_key = color or ""
        if icon_key and not self._status_icon_handle_for(color):
            icon_key = ""
        tooltip = build_tray_tooltip(
            phase=phase,
            launch_method=self._launch_method,
            preset_name=self._preset_name,
            language=self._language(),
        )
        if icon_key != self._status_icon_key or tooltip != self._tooltip:
            self._status_icon_key = icon_key
            self._tooltip = tooltip
            self._modify_icon()

        if previous_phase and previous_phase != phase:
            self._notify_launch_transition(previous_phase, phase)
        popup = self._popup
        if popup is not None and popup.isVisible():
            # Открытое меню живо: шапка, главный пункт и список пресетов следуют за состоянием.
            popup.set_model(self._menu_model())

    def _notify_launch_transition(self, previous_phase: str, phase: str) -> None:
        if phase in {"starting", "autostart_pending", "running"}:
            # Перезапуск: «остановлен» сразу сменился запуском — не шумим.
            self._cancel_stopped_notification()
        if self._is_window_visible():
            return
        language = self._language()
        if phase == "running" and previous_phase in {"starting", "autostart_pending"}:
            if self._preset_name:
                body = tr_catalog(
                    "tray.notify.started.body",
                    language=language,
                    default="Обход блокировок активен · пресет: {preset}",
                ).format(preset=self._preset_name)
            else:
                body = tr_catalog(
                    "tray.notify.started.body_no_preset",
                    language=language,
                    default="Обход блокировок активен",
                )
            self.show_notification(
                tr_catalog("tray.notify.started.title", language=language, default="Zapret запущен"),
                body,
            )
        elif phase == "stopped" and previous_phase == "stopping":
            self._schedule_stopped_notification()

    def _schedule_stopped_notification(self) -> None:
        self._cancel_stopped_notification()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(self._show_stopped_notification)
        timer.start(self.STOPPED_NOTIFY_DELAY_MS)
        self._stopped_notify_timer = timer

    def _cancel_stopped_notification(self) -> None:
        timer = self._stopped_notify_timer
        self._stopped_notify_timer = None
        if timer is not None:
            timer.stop()

    def _show_stopped_notification(self) -> None:
        self._stopped_notify_timer = None
        if self._launch_phase != "stopped" or self._is_window_visible():
            return
        language = self._language()
        self.show_notification(
            tr_catalog("tray.notify.stopped.title", language=language, default="Zapret остановлен"),
            tr_catalog("tray.notify.stopped.body", language=language, default="Обход блокировок выключен"),
        )

    def _is_window_visible(self) -> bool:
        try:
            return bool(self.window_port.is_visible())
        except Exception:
            return False

    def _language(self) -> str | None:
        try:
            return self.window_port.ui_language() or None
        except Exception:
            return None

    # ---- клики по иконке -----------------------------------------------

    def _handle_native_callback(self, callback_code: int, anchor_x: int | None = None, anchor_y: int | None = None) -> None:
        if callback_code == WM_CONTEXTMENU:
            QTimer.singleShot(0, lambda: self.show_context_menu(anchor_x=anchor_x, anchor_y=anchor_y))
            return

        if callback_code == WM_RBUTTONUP:
            QTimer.singleShot(0, lambda: self.show_context_menu(anchor_x=None, anchor_y=None))
            return

        if callback_code in (WM_LBUTTONUP, WM_LBUTTONDBLCLK, NIN_SELECT, NIN_KEYSELECT):
            self._schedule_visibility_toggle()

    def _handle_taskbar_recreated(self) -> None:
        QTimer.singleShot(0, self.recreate_icon)

    def _schedule_visibility_toggle(self) -> None:
        if self._toggle_request_pending:
            return
        self._toggle_request_pending = True
        QTimer.singleShot(0, self._run_scheduled_visibility_toggle)

    def _run_scheduled_visibility_toggle(self) -> None:
        self._toggle_request_pending = False
        now = time.monotonic()
        if now - self._last_toggle_monotonic < 0.30:
            return
        self._last_toggle_monotonic = now
        self.toggle_window_visibility()

    def toggle_window_visibility(self) -> None:
        if self.window_port.is_visible():
            self.hide_to_tray(show_hint=False)
            return

        self.show_window()

    # ---- меню ------------------------------------------------------------

    def show_context_menu(self, anchor_x: int | None = None, anchor_y: int | None = None) -> None:
        # Координаты из сообщения Windows даны в точках экрана без учёта масштаба,
        # а Qt считает с масштабом, поэтому меню ставится по положению курсора.
        _ = anchor_x, anchor_y
        popup = self._ensure_popup()
        if popup.isVisible():
            return
        popup.set_model(self._menu_model())
        try:
            popup.open_at(QCursor.pos())
        except Exception as e:
            log(f"Не удалось показать tray menu: {e}", "WARNING")
            return
        # Список пресетов перечитывается в фоне; когда он придёт, открытое меню обновится само.
        try:
            self._tray_feature.refresh_preset_snapshot()
        except Exception:
            pass

    def _ensure_popup(self):
        if self._popup is None:
            self._popup = self._create_popup()
            self._popup.commandTriggered.connect(self._run_menu_command)
        return self._popup

    def _create_popup(self):
        from ui.tray_menu.popup import TrayMenuPopup

        return TrayMenuPopup()

    def _menu_launch_phase(self) -> str:
        try:
            return normalize_launch_phase(self._tray_feature.launch_phase())
        except Exception:
            return normalize_launch_phase(self._launch_phase)

    def _menu_snapshot(self):
        try:
            return self._tray_feature.preset_snapshot()
        except Exception:
            return None

    def _menu_state(self) -> TrayMenuState:
        from settings.mode import is_preset_launch_method

        snapshot = self._menu_snapshot()
        has_presets = snapshot is not None and is_preset_launch_method(snapshot.launch_method)
        try:
            telegram_label = str(self._tray_feature.telegram_proxy_label())
        except Exception:
            telegram_label = "Telegram Proxy"
        return TrayMenuState(
            phase=self._menu_launch_phase(),
            launch_method=self._launch_method or ("" if snapshot is None else snapshot.launch_method),
            preset_name=self._preset_name or ("" if snapshot is None else snapshot.selected_display_name),
            presets=tuple(snapshot.presets) if has_presets else (),
            selected_preset_file=snapshot.selected_file_name if has_presets else "",
            has_presets=has_presets,
            window_visible=self._is_window_visible(),
            telegram_label=telegram_label,
            windows_11=self._is_windows_11_or_newer(),
            language=self._language(),
        )

    def _menu_model(self):
        return build_tray_menu(self._menu_state())

    def _run_menu_command(self, command: str, arg=None) -> None:
        if command == "activate_preset":
            file_name, display_name = arg
            self._tray_feature.activate_preset(file_name, display_name)
        elif command == "set_window_opacity":
            self._set_window_opacity(int(arg))
        elif command == "toggle_dpi":
            self._tray_feature.toggle_dpi()
        elif command == "restart_dpi":
            self._tray_feature.restart_dpi()
        elif command == "toggle_telegram_proxy":
            self._toggle_tg_proxy()
        elif command == "show_window":
            self.show_window()
        elif command == "toggle_window":
            self._toggle_primary_visibility_action()
        elif command == "show_console":
            self.show_console()
        elif command == "exit_only":
            self.exit_only()
        elif command == "exit_and_stop":
            self.exit_and_stop()
        else:
            log(f"Неизвестная команда меню трея: {command}", "WARNING")

    def _toggle_primary_visibility_action(self) -> None:
        try:
            if self.window_port.is_visible():
                self.hide_to_tray(show_hint=False)
            else:
                self.show_window()
        except Exception:
            pass

    # ---- уведомления и действия ---------------------------------------

    def show_notification(self, title, message, msec=5000):
        if sys.platform != "win32" or not self._hwnd or not self._icon_visible:
            return

        data = self._build_notify_icon_data(NIF_INFO)
        data.szInfoTitle = _truncate_text(str(title or ""), 64)
        data.szInfo = _truncate_text(str(message or ""), 256)
        data.dwInfoFlags = NIIF_INFO if str(title or "").strip() else NIIF_NONE
        data.uTimeout = int(max(1000, msec))
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def _connect_proxy_status_signal(self) -> None:
        self._tray_feature.connect_telegram_proxy_status_changed(self._on_tg_proxy_status_changed)

    def _toggle_tg_proxy(self):
        self._tray_feature.toggle_telegram_proxy()

    def _on_tg_proxy_status_changed(self, running: bool):
        self._tray_feature.set_telegram_proxy_enabled(bool(running))

    def _save_window_geometry(self):
        self.window_port.persist_geometry()

    def _cleanup_transient_overlays(self) -> None:
        try:
            from ui.widgets.strategies_tooltip import strategies_tooltip_manager
            strategies_tooltip_manager.hide_immediately()
        except Exception:
            pass

    def hide_to_tray(self, show_hint: bool = True) -> bool:
        try:
            self._cleanup_transient_overlays()
        except Exception:
            pass

        try:
            self._save_window_geometry()
        except Exception:
            pass

        try:
            self.window_port.release_input_interaction_states()
        except Exception:
            pass

        try:
            self.window_port.hide()
        except Exception as e:
            log(f"Не удалось скрыть окно в трей: {e}", "WARNING")
            return False

        if not show_hint:
            return True

        if not self._tray_hint_shown_this_session:
            try:
                self.show_notification(
                    "Zapret продолжает работать",
                    "Свернуто в трей. Кликните по иконке, чтобы открыть окно.",
                )
                self._tray_hint_shown_this_session = True
            except Exception:
                pass

        return True

    def exit_only(self):
        self.window_port.request_exit(stop_dpi=False)

    def exit_and_stop(self):
        self.window_port.request_exit(stop_dpi=True)

    def show_console(self):
        cmd, ok = self.window_port.prompt_console_command()
        if not ok or not cmd:
            return

        if cmd.lower() == "ркн":
            self._tray_feature.toggle_discord_restart(
                status_callback=lambda m: self.show_notification("Консоль", m),
                confirm_disable=self._confirm_disable_discord_restart,
            )

    def show_window(self):
        try:
            self._cleanup_transient_overlays()
        except Exception:
            pass

        try:
            self.window_port.show()
        except Exception:
            pass

    def _confirm_disable_discord_restart(self) -> bool:
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Отключение автоперезапуска Discord")
        box.setText("Вы действительно хотите отключить автоматический перезапуск Discord?")
        box.setInformativeText(
            "После отключения вам придётся вручную перезапускать Discord при смене стратегии."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        set_message_box_button_accessibility(
            box,
            yes_button=box.button(QMessageBox.StandardButton.Yes),
            cancel_button=box.button(QMessageBox.StandardButton.No),
            yes_name="Отключить автоперезапуск Discord",
            yes_description=(
                "Discord больше не будет перезапускаться автоматически при смене стратегии."
            ),
            cancel_name="Не отключать автоперезапуск Discord",
            cancel_description="Автоперезапуск Discord останется включённым.",
        )
        return box.exec() == QMessageBox.StandardButton.Yes

    def _set_window_opacity(self, value: int) -> None:
        self._tray_feature.apply_window_opacity(int(value))

    def _is_windows_11_or_newer(self) -> bool:
        try:
            return sys.platform == "win32" and sys.getwindowsversion().build >= 22000
        except Exception:
            return False


class _TrayMessageWindow:
    """Скрытое top-level окно для callback-сообщений notification area."""

    def __init__(self, owner: SystemTrayManager):
        self.owner = owner
        self.hwnd = None
        self.taskbar_created_message = None
        self._wndproc = None
        self._instance = None
        self._class_registered = False

        if sys.platform == "win32":
            self._create_window()

    def _create_window(self) -> None:
        self.taskbar_created_message = user32.RegisterWindowMessageW("TaskbarCreated")
        instance = kernel32.GetModuleHandleW(None)
        self._instance = instance

        self._wndproc = WNDPROC(self._dispatch)
        window_class = WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.lpszClassName = self.owner._class_name

        atom = user32.RegisterClassExW(ctypes.byref(window_class))
        if not atom:
            last_error = ctypes.get_last_error()
            # 1410 = class already exists.
            if last_error != 1410:
                raise OSError(last_error, "Не удалось зарегистрировать класс tray window")
        else:
            self._class_registered = True

        hwnd = user32.CreateWindowExW(
            0,
            self.owner._class_name,
            self.owner._class_name,
            0,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            0,
            0,
            None,
            None,
            instance,
            None,
        )
        if not hwnd:
            self._unregister_class()
            raise OSError(ctypes.get_last_error(), "Не удалось создать native tray window")

        self.hwnd = hwnd

    def destroy(self) -> None:
        hwnd = self.hwnd
        self.hwnd = None
        if sys.platform != "win32" or not hwnd:
            return

        try:
            user32.DestroyWindow(hwnd)
        except Exception:
            pass
        self._unregister_class()

    def _unregister_class(self) -> None:
        if sys.platform != "win32" or not self._class_registered:
            return
        instance = self._instance or kernel32.GetModuleHandleW(None)
        try:
            if not bool(user32.UnregisterClassW(self.owner._class_name, instance)):
                return
        except Exception:
            return
        self._class_registered = False
        self._wndproc = None

    def _dispatch(self, hwnd, message, w_param, l_param):
        try:
            if message == TRAY_CALLBACK_MESSAGE:
                callback_code = _loword(int(l_param))
                anchor_x = _get_x_lparam(int(w_param))
                anchor_y = _get_y_lparam(int(w_param))
                self.owner._handle_native_callback(callback_code, anchor_x=anchor_x, anchor_y=anchor_y)
                return 0

            if self.taskbar_created_message and message == self.taskbar_created_message:
                self.owner._handle_taskbar_recreated()
                return 0

            if message in (WM_CLOSE, WM_DESTROY):
                return 0
        except Exception as e:
            log(f"Ошибка обработки native tray message: {e}", "DEBUG")

        return user32.DefWindowProcW(hwnd, message, w_param, l_param)
