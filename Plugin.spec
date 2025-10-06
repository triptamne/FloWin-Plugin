# plugin.spec
# PyInstaller >=6
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Ruta del entrypoint
import os
script = os.path.abspath("Plugin.py")

if not os.path.exists(script):
    raise SystemExit(f"[SPEC] No se encontró el entry script en: {script}")

# Incluir la carpeta de fonts completa dentro del bundle en "fonts\DejaVuSans"
datas = []
fonts_dir = os.path.join(os.path.dirname(script), "fonts", "DejaVuSans")
if os.path.isdir(fonts_dir):
    for name in os.listdir(fonts_dir):
        if name.lower().endswith(".ttf"):
            datas.append((os.path.join(fonts_dir, name), "fonts\\DejaVuSans"))

a = Analysis(
    [script],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("win32print") + collect_submodules("win32ui"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="Plugin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,      # Flask/console. Si no quieres consola, pon False
    disable_windowed_traceback=False,
    target_arch=None,  # lo decide el Python (x86/x64)
    uac_admin=False,
    uac_uiaccess=False,
    argv_emulation=False,
    codesign_identity=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Plugin",
)
