# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller configuration for the Alpaca Assist desktop application."""

import sys
from pathlib import Path


project_root = Path(SPECPATH)
icon_dir = project_root / "assets"

if sys.platform == "win32":
    executable_icon = icon_dir / "alpaca-assist.ico"
elif sys.platform == "darwin":
    executable_icon = icon_dir / "alpaca-assist.icns"
else:
    executable_icon = icon_dir / "alpaca-assist.png"

a = Analysis(
    [str(project_root / "webview_app.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "web"), "web"),
        (str(icon_dir / "alpaca-assist.png"), "assets"),
    ],
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    [],
    name="AlpacaAssist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(executable_icon),
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="AlpacaAssist.app",
        icon=str(icon_dir / "alpaca-assist.icns"),
        bundle_identifier="com.carlosdcastillo.alpacaassist",
        info_plist={
            "CFBundleName": "Alpaca Assist",
            "CFBundleDisplayName": "Alpaca Assist",
            "NSHighResolutionCapable": True,
        },
    )
