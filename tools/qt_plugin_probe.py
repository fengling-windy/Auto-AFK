"""Reproduce Qt's own plugin-load pattern against qwindows.dll.

Qt loads a platform plugin twice: once to read its metadata
(QLibraryPrivate::updatePluginState -> load, query, unload), then again to
instantiate the QPlatformIntegration.  This probe repeats that exact pattern
and reports the Win32 error of every step, so we can tell "missing import"
(126) apart from "DllMain failed" (1114) and from "second load fails".

Usage:  python tools/qt_plugin_probe.py
"""
import ctypes
import os
import sys
from ctypes import wintypes

NX_MAIN = r"E:\MuMuPlayer\nx_main"
PLUGIN = os.path.join(NX_MAIN, "plugins", "platforms", "qwindows.dll")

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
k32.LoadLibraryExW.restype = wintypes.HMODULE
k32.FreeLibrary.argtypes = [wintypes.HMODULE]
k32.FreeLibrary.restype = wintypes.BOOL
k32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
k32.SetDllDirectoryW.restype = wintypes.BOOL


def err():
    code = ctypes.get_last_error()
    return code, ctypes.FormatError(code).strip()


def attempt(label, flags):
    ctypes.set_last_error(0)
    mod = k32.LoadLibraryExW(PLUGIN, None, flags)
    if mod:
        print("  %-32s OK   handle=0x%x" % (label, mod))
    else:
        code, text = err()
        print("  %-32s FAIL win32=%d (0x%X) %s" % (label, code, code, text))
    return mod


def main():
    print("plugin:", PLUGIN, "exists=", os.path.exists(PLUGIN))
    print("process exe dir:", os.path.dirname(sys.executable))

    print("\n[1] default search order (exe dir = python, NOT nx_main)")
    attempt("LOAD_LIBRARY_SEARCH_DEFAULT", 0x1000)
    attempt("plain 0", 0)

    print("\n[2] with nx_main on the DLL directory")
    k32.SetDllDirectoryW(NX_MAIN)
    os.environ["PATH"] = NX_MAIN + os.pathsep + os.environ.get("PATH", "")
    mod = attempt("LOAD_LIBRARY_SEARCH_DEFAULT", 0x1000)
    if not mod:
        mod = attempt("plain 0", 0)

    if mod:
        print("\n[3] unload, then load again like Qt does for real instantiation")
        ok = k32.FreeLibrary(mod)
        print("  FreeLibrary ->", bool(ok))
        mod2 = attempt("reload after unload", 0x1000)
        if not mod2:
            mod2 = attempt("reload plain 0", 0)
        if mod2:
            mod3 = k32.LoadLibraryExW(os.path.join(NX_MAIN, "Qt5Gui.dll"), None, 0x1000)
            print("  Qt5Gui.dll direct load -> 0x%x" % (mod3 or 0))

    print("\n[4] mapped modules of interest in this process")
    for name in ("AudioDevProps2.dll", "Qt5Gui.dll", "freetype.dll", "NahimicAPO4.dll"):
        h = k32.GetModuleHandleW(name)
        print("  %-20s %s" % (name, ("loaded 0x%x" % h) if h else "not loaded"))


if __name__ == "__main__":
    main()
