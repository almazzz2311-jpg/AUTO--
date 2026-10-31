"""Win32 hotkeys and per-user startup. No keyboard hooks or polling."""
import ctypes
from ctypes import wintypes
import os
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, Signal, QObject


MODIFIERS = {'CTRL': 2, 'SHIFT': 4, 'ALT': 1, 'WIN': 8}


def parse_hotkey(text):
    parts = text.upper().replace(' ', '').split('+')
    if len(parts) < 2 or any(p not in MODIFIERS for p in parts[:-1]):
        raise ValueError('Используйте сочетание вроде Ctrl+Shift+A')
    key = parts[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key)
    elif key.startswith('F') and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError('Поддерживаются буквы A–Z, цифры и F1–F24')
    modifiers = 0x4000  # MOD_NOREPEAT
    for part in parts[:-1]:
        modifiers |= MODIFIERS[part]
    return modifiers, vk


class HotkeySignals(QObject):
    activated = Signal(str)


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, application):
        super().__init__()
        self.application = application
        self.signals = HotkeySignals()
        self.registered = {}
        self.application.installNativeEventFilter(self)

    def configure(self, combinations):
        parsed = {action: parse_hotkey(text) for action, text in combinations.items()}
        if len(set(parsed.values())) != len(parsed):
            raise ValueError('Горячие клавиши должны отличаться')
        if sys.platform != 'win32':
            raise RuntimeError('Глобальные горячие клавиши доступны только в Windows')
        previous = dict(self.registered)
        self.clear()
        try:
            for index, (action, (modifiers, key)) in enumerate(parsed.items(), 1):
                if not ctypes.windll.user32.RegisterHotKey(None, index, modifiers, key):
                    raise ValueError(f'Сочетание {combinations[action]} занято другой программой')
                self.registered[index] = (action, modifiers, key)
        except Exception:
            self.clear()
            for index, (action, modifiers, key) in previous.items():
                if ctypes.windll.user32.RegisterHotKey(None, index, modifiers, key):
                    self.registered[index] = (action, modifiers, key)
            raise

    def clear(self):
        if sys.platform == 'win32':
            for index in self.registered:
                ctypes.windll.user32.UnregisterHotKey(None, index)
        self.registered.clear()

    def nativeEventFilter(self, event_type, message):
        if sys.platform == 'win32' and event_type in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam in self.registered:
                self.signals.activated.emit(self.registered[msg.wParam][0])
                return True, 0
        return False, 0


def set_startup(enabled: bool):
    if sys.platform != 'win32':
        if enabled:
            raise RuntimeError('Автозагрузка доступна только в Windows')
        return
    import winreg
    executable = sys.executable
    if getattr(sys, 'frozen', False):
        command = f'"{executable}" --tray'
    else:
        from pathlib import Path
        main = Path(__file__).resolve().parents[2] / 'main.py'
        windowless = os.path.join(os.path.dirname(executable), 'pythonw.exe')
        command = f'"{windowless if os.path.exists(windowless) else executable}" "{main}" --tray'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            winreg.SetValueEx(key, 'AI Screen Assistant', 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, 'AI Screen Assistant')
            except FileNotFoundError:
                pass


def startup_enabled():
    if sys.platform != 'win32':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            value, _ = winreg.QueryValueEx(key, 'AI Screen Assistant')
            return bool(value)
    except FileNotFoundError:
        return False
