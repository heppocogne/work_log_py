"""Windows API 呼び出しの集約。

使う API は少数なので、外部パッケージ (pywin32) なしで ctypes だけで実装している。
Windows 以外では収集系の関数は WinApiUnavailable を送出する (開発・テスト用に DB 操作だけは動く)。
"""

from __future__ import annotations

import os
import socket
import sys
from dataclasses import dataclass
from typing import Any

IS_WINDOWS = sys.platform == "win32"


class WinApiUnavailable(RuntimeError):
    pass


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    process: str | None  # 実行ファイル名 (例: POWERPNT.EXE)。取れなければ None
    pid: int | None


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
    _user32.GetLastInputInfo.restype = wintypes.BOOL
    _kernel32.GetTickCount.restype = wintypes.DWORD

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _ERROR_ALREADY_EXISTS = 183


def _require_windows() -> None:
    if not IS_WINDOWS:
        raise WinApiUnavailable("ウィンドウ監視は Windows でのみ使えます")


def _process_name(pid: int) -> str | None:
    h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None  # 管理者権限で動くアプリなど
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return os.path.basename(buf.value) or None
    finally:
        _kernel32.CloseHandle(h)


def foreground_window() -> WindowInfo | None:
    """前面ウィンドウの情報。取れない (0 が返る) 場合は None。"""
    _require_windows()
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = _process_name(pid.value) if pid.value else None
    return WindowInfo(int(hwnd), buf.value, process, pid.value or None)


def idle_ms() -> int:
    """最後のキーボード・マウス操作からの経過ミリ秒。"""
    _require_windows()
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "GetLastInputInfo failed")
    # GetTickCount は 32bit で約49.7日ごとに桁あふれするので差を 32bit に丸める
    return (_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF


def set_foreground(hwnd: int) -> bool:
    """GUI 用: 小窓を閉じたあと直前のウィンドウへフォーカスを戻す。失敗しても無視してよい。"""
    if not IS_WINDOWS or not hwnd:
        return False
    return bool(_user32.SetForegroundWindow(hwnd))


class SingleInstanceLock:
    """二重起動防止。名前付きミューテックス、使えなければ localhost のポートへの bind。"""

    def __init__(self, name: str, port: int) -> None:
        self.name = name
        self.port = port
        self._handle: Any = None
        self._sock: socket.socket | None = None

    def acquire(self) -> bool:
        if IS_WINDOWS:
            try:
                h = _kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
                err = ctypes.get_last_error()
                if h:
                    if err == _ERROR_ALREADY_EXISTS:
                        _kernel32.CloseHandle(h)
                        return False
                    self._handle = h
                    return True
            except (AttributeError, OSError):
                pass
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: 他プロセスによる横取りを防ぐ
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("127.0.0.1", self.port))
        except OSError:
            sock.close()
            return False
        self._sock = sock
        return True

    def release(self) -> None:
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> SingleInstanceLock:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
