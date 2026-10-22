# -*- mode: python ; coding: utf-8 -*-
#
# 外脑 · Cordis Brain —— Windows 桌面客户端打包（PyInstaller onedir）
#   python -m PyInstaller --noconfirm cordis-desktop.spec     # 在 desktop/ 目录下执行
# 产物：desktop/dist/cordis-desktop/cordis-desktop.exe
#
# 渲染后端优先 Qt WebEngine（自带 Chromium）；本机装的是 PySide6，
# 所以 hiddenimports 指向 PySide6 / qtpy（pywebview 6 通过 qtpy 选择绑定）。
# 若目标机没装 PySide6，运行时会自动回退系统 WebView2(Edge)。

import os

_ICO = os.path.join(SPECPATH, 'cordis.ico')

a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    binaries=[],
    datas=[(_ICO, '.')],
    hiddenimports=[
        'webview.platforms.qt',
        'qtpy',
        'qtpy.QtCore',
        'qtpy.QtGui',
        'qtpy.QtWidgets',
        'qtpy.QtNetwork',
        'qtpy.QtWebChannel',
        'qtpy.QtWebEngineCore',
        'qtpy.QtWebEngineWidgets',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtWebChannel',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='cordis-desktop',
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
    icon=[_ICO],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='cordis-desktop',
)
