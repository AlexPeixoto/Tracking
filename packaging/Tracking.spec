# PyInstaller spec for Tracking.exe. Build with build.ps1 (it makes the icon first).
# One-folder build: starts fast; zip dist/Tracking to move it to another PC.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

# Data files libraries load at runtime (PyInstaller can't see these imports).
datas = (
    collect_data_files("guessit")
    + collect_data_files("babelfish")
    + collect_data_files("rebulk")
    + collect_data_files("fake_useragent")
)
binaries = collect_dynamic_libs("pymediainfo")  # MediaInfo.dll

# Qt modules the app never uses; PySide6 ships them all and they add ~500 MB.
QT_UNUSED = [
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets",
    "QtQuick", "QtQuick3D", "QtQuickWidgets", "QtQuickControls2", "QtQml", "Qt3DCore",
    "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras", "QtMultimedia",
    "QtMultimediaWidgets", "QtCharts", "QtDataVisualization", "QtGraphs", "QtPdf",
    "QtPdfWidgets", "QtBluetooth", "QtNfc", "QtPositioning", "QtLocation", "QtSensors",
    "QtSerialPort", "QtSerialBus", "QtDesigner", "QtHelp", "QtSql", "QtTest", "QtRemoteObjects",
    "QtScxml", "QtSpatialAudio", "QtTextToSpeech", "QtHttpServer", "QtOpenGLWidgets",
]
excludes = [f"PySide6.{m}" for m in QT_UNUSED] + ["tkinter", "pytest", "PyInstaller"]

a = Analysis(
    [str(ROOT / "packaging" / "tracking_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    # babelfish / guessit / rebulk import their converters and rules by name at runtime.
    hiddenimports=(collect_submodules("babelfish") + collect_submodules("guessit")
                   + collect_submodules("rebulk")),
    excludes=excludes,
)

# Drop the DLLs / translations of the excluded Qt modules that the PySide6 hook
# still copies.
def _unused(name: str) -> bool:
    low = name.lower().replace("\\", "/")
    return any(f"qt6{m[2:].lower()}" in low for m in QT_UNUSED) or "/translations/qtwebengine" in low

a.binaries = [b for b in a.binaries if not _unused(b[0])]
a.datas = [d for d in a.datas if not _unused(d[0])]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Tracking",
    icon=str(ROOT / "build" / "tracking.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Tracking", upx=False)
