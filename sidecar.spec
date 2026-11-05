# sidecar.spec — empacota o Analítico de PDVs como sidecar do Darwin Connect.
# Gera dist/verificador-posicao/ (onedir). Copie essa pasta para
#   DarwinConnect/src-tauri/resources/analitico/
# Build:  pyinstaller sidecar.spec --noconfirm
# (precisa: pip install pyinstaller  +  as deps do requirements.txt no mesmo venv)

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

datas = [
    ("templates", "templates"),
    ("static", "static"),
    ("models", "models"),          # pdv_corredor.onnx — o motor precisa dele
    ("test_images", "test_images"),
]
# O hook do cv2 às vezes NÃO coleta config.py/config-3.py → ImportError "missing configuration
# file" no runtime. Coleta explícita deixa o build determinístico.
datas += collect_data_files("cv2")

# mongo_store importa pymongo/bcrypt DENTRO das funções (import tardio) — o PyInstaller
# não os enxerga sozinho. pymongo[srv] puxa dnspython. opencv/numpy/flask vêm pelos imports.
hiddenimports = [
    "pymongo", "bcrypt", "dns", "dns.resolver",
    "cv2", "numpy", "flask",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "torchvision", "matplotlib"],  # só treino usa; não vão pro runtime
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="verificador-posicao",
    debug=False,
    strip=False,
    upx=False,
    console=True,   # o Darwin Connect lê o stdout (INGEST_READY <token>); no Windows roda sem janela (CREATE_NO_WINDOW)
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="verificador-posicao",
)
