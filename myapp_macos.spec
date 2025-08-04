# myapp.spec
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py','chat_app.py', 'chat_tab.py', 'chat_state.py', 'expansion_language.py', 'find_dialog.py', 'preferences.py', 'syntax_text.py', 'text_utils.py', 'token_cache.py', 'tooltip.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('alpaca.png', '.'),  # Include PNG icon
        ('alpaca.icns', '.'), # Include ICNS icon if you have it
    ],
    hiddenimports=['PIL', 'PIL._tkinter_finder', 'PIL.Image', 'PIL.ImageTk'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AlpacaAssist',           # Name of your executable
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,               # Set to True if you want console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    cofile=None,
    icon='alpaca.icns',        # This sets the executable icon
)
app = BUNDLE(
    exe,
    name='AlpacaAssist.app',
    icon='alpaca.icns',  # Use .icns for the app bundle icon
    bundle_identifier='com.yourcompany.alpacaassist',  # Change to your identifier
    info_plist={
        'CFBundleName': 'AlpacaAssist',
        'CFBundleDisplayName': 'AlpacaAssist',
        'CFBundleShortVersionString': '0.06',
        'CFBundleVersion': '0.06',
        'NSHighResolutionCapable': 'True',
        'LSMinimumSystemVersion': '10.13.0',  # Adjust as needed
    }
)
