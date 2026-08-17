# -*- coding: utf-8 -*-
"""代写小助手 —— 键盘模拟后端

基于 Windows 原生 SendInput API 实现，支持中文等 Unicode 字符逐字输入。
- 无需第三方依赖（仅标准库 ctypes）
- 无需管理员权限
- 支持剪贴板粘贴模式
"""

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ---------------- Win32 常量 ----------------
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_V = 0x56

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


# ---------------- Win32 结构体 ----------------
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTUNION),
    ]


# ---------------- 设置函数签名（防止 64 位指针被截断）----------------
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE


# ---------------- 内部工具 ----------------
def _keybd_input(wVk=0, wScan=0, dwFlags=0):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = wVk
    inp.union.ki.wScan = wScan
    inp.union.ki.dwFlags = dwFlags
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = None
    return inp


def _send_inputs(inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    p = ctypes.cast(arr, ctypes.POINTER(INPUT))
    sent = user32.SendInput(n, p, ctypes.sizeof(INPUT))
    if sent != n:
        raise OSError("SendInput 失败：期望 %d 个事件，实际发送 %d 个" % (n, sent))


def _utf16_units(ch):
    """把单个字符拆成 UTF-16 编码单元（处理 emoji 等代理对）。"""
    code = ord(ch)
    if code < 0x10000:
        return [code]
    code -= 0x10000
    hi = 0xD800 + (code >> 10)
    lo = 0xDC00 + (code & 0x3FF)
    return [hi, lo]


# ---------------- 对外接口 ----------------
def send_unicode_char(ch):
    """发送单个 Unicode 字符（按下 + 抬起）。"""
    for unit in _utf16_units(ch):
        _send_inputs([
            _keybd_input(wScan=unit, dwFlags=KEYEVENTF_UNICODE),
            _keybd_input(wScan=unit, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
        ])


def send_key(key_vk):
    """发送一个虚拟键（按下 + 抬起）。"""
    _send_inputs([
        _keybd_input(wVk=key_vk, dwFlags=0),
        _keybd_input(wVk=key_vk, dwFlags=KEYEVENTF_KEYUP),
    ])


def send_ctrl_v():
    """发送 Ctrl+V（粘贴）。"""
    _send_inputs([
        _keybd_input(wVk=VK_CONTROL, dwFlags=0),
        _keybd_input(wVk=VK_V, dwFlags=0),
        _keybd_input(wVk=VK_V, dwFlags=KEYEVENTF_KEYUP),
        _keybd_input(wVk=VK_CONTROL, dwFlags=KEYEVENTF_KEYUP),
    ])


def set_clipboard_text(text):
    """把文本写入系统剪贴板（线程安全，UTF-16）。"""
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not user32.OpenClipboard(0):
        raise OSError("无法打开剪贴板")
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            raise OSError("分配剪贴板内存失败")
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            raise OSError("锁定剪贴板内存失败")
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            raise OSError("写入剪贴板失败")
    finally:
        user32.CloseClipboard()


def type_text(text, interval=0.03, randomize=False, stop_check=None, progress=None):
    """逐字输入文本。

    参数：
        text       要输入的文本
        interval   每字间隔（秒）
        randomize  是否加入随机抖动（模拟人手）
        stop_check 可调用对象，返回 True 时中断输入
        progress   可选回调 progress(已输入字符数, 总字符数)

    返回：
        True  全部输入完成
        False 被 stop_check 中断
    """
    import random

    total = len(text)
    for i, ch in enumerate(text):
        if stop_check is not None and stop_check():
            return False

        if ch == "\r":
            pass  # 忽略回车符（\r\n 里的 \r）
        elif ch == "\n":
            send_key(VK_RETURN)  # 换行按回车输入
        elif ch == "\t":
            send_key(VK_TAB)
        else:
            send_unicode_char(ch)

        if randomize:
            delay = interval * random.uniform(0.5, 1.9)
        else:
            delay = interval
        if delay > 0:
            time.sleep(delay)

        if progress is not None:
            progress(i + 1, total)

    return True
