import os
import sys
from pathlib import Path

# Resolve CPython's own DLLs (OpenSSL for _ssl, libffi, ...) before same-named copies
# that other software puts on PATH; a mismatched libcrypto breaks HTTPS in the build.
os.environ["PATH"] = os.pathsep.join([str(Path(sys.base_prefix) / "DLLs"), os.environ["PATH"]])

ROOT = Path(SPECPATH).parent
a = Analysis([str(Path(SPECPATH) / "taskbar_launcher.py")],
             pathex=[str(ROOT / "src")], binaries=[], datas=[],
             hiddenimports=["keyring.backends.Windows", "win32ctypes.pywin32.win32cred"],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="QuotaPanel", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(ROOT / "assets" / "quotabubble.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="QuotaPanel")
