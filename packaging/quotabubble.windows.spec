# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import tomllib
from pathlib import Path

# Resolve CPython's own DLLs (OpenSSL for _ssl, libffi, ...) before same-named copies
# that other software puts on PATH; a mismatched libcrypto breaks HTTPS in the build.
os.environ["PATH"] = os.pathsep.join([str(Path(sys.base_prefix) / "DLLs"), os.environ["PATH"]])

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent

# SignPath Foundation requires the signed binary's file metadata to be set,
# so the version resource is generated from pyproject.toml's version.
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
VERSION_TUPLE = (*(int(part) for part in VERSION.split(".")[:3]), 0)
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Izzet Yildirim"),
                        StringStruct("FileDescription", "QuotaBubble"),
                        StringStruct("FileVersion", ".".join(map(str, VERSION_TUPLE))),
                        StringStruct("InternalName", "QuotaBubble"),
                        StringStruct("LegalCopyright", "Copyright (c) 2026 Izzet Yildirim. MIT license."),
                        StringStruct("OriginalFilename", "QuotaBubble.exe"),
                        StringStruct("ProductName", "QuotaBubble"),
                        StringStruct("ProductVersion", ".".join(map(str, VERSION_TUPLE))),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    # keyring picks its backend via importlib.metadata entry points at
    # runtime, so PyInstaller's static analysis never sees the Windows
    # backend chain (keyring.backends.Windows -> win32ctypes.pywin32.* ->
    # win32ctypes.core) as reachable and silently omits it, which makes the
    # OS keyring unusable in the frozen build even though it works fine in
    # dev. win32ctypes.core also redirects e.g. "win32ctypes.core._common"
    # to "win32ctypes.core.ctypes._common" via a custom sys.meta_path finder
    # at runtime, which static analysis (and PyInstaller's own built-in
    # hook-win32ctypes.core.py) can't see through either, so the real
    # win32ctypes.core.ctypes.* files are listed explicitly below.
    hiddenimports=[
        "keyring.backends.Windows",
        "win32ctypes.core",
        "win32ctypes.core.ctypes",
        "win32ctypes.core.ctypes._authentication",
        "win32ctypes.core.ctypes._common",
        "win32ctypes.core.ctypes._dll",
        "win32ctypes.core.ctypes._nl_support",
        "win32ctypes.core.ctypes._resource",
        "win32ctypes.core.ctypes._system_information",
        "win32ctypes.core.ctypes._time",
        "win32ctypes.core.ctypes._util",
        "win32ctypes.pywin32.pywintypes",
        "win32ctypes.pywin32.win32api",
        "win32ctypes.pywin32.win32cred",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QuotaBubble",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "assets" / "quotabubble.ico"),
    version=VERSION_INFO,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="QuotaBubble",
)
