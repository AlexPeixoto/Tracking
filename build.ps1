# Build dist\Tracking\Tracking.exe and run its self-test.
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    python -m venv .venv
}
# Every build: new requirements (e.g. comtypes for Android devices) get installed too.
& $py -m pip install -q -r requirements.txt
& $py -m pip install -q pyinstaller pillow

$env:PYTHONPATH = $PSScriptRoot
& $py packaging\make_icon.py build\tracking.ico
& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller packaging\Tracking.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# The exe has no console, so the self-test writes its report next to the log.
$p = Start-Process dist\Tracking\Tracking.exe -ArgumentList "--selftest" -Wait -PassThru
Get-Content "$env:LOCALAPPDATA\Tracking\logs\selftest.txt"
if ($p.ExitCode -ne 0) { throw "Self-test failed (exit $($p.ExitCode))" }

$size = (Get-ChildItem dist\Tracking -Recurse | Measure-Object Length -Sum).Sum / 1MB
"Built dist\Tracking\Tracking.exe ({0:N0} MB folder)" -f $size
