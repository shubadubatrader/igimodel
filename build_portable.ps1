param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$dependencyRoot = Join-Path $projectRoot ".deps"
$env:PYTHONPATH = $dependencyRoot
$env:TCL_LIBRARY = Join-Path $projectRoot "vendor\python_tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $projectRoot "vendor\python_tcl\tk8.6"

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name IGIModel `
    --paths $dependencyRoot `
    --add-data "vendor;vendor" `
    --add-data "profiles;profiles" `
    --exclude-module pandas `
    --exclude-module pytest `
    --exclude-module matplotlib `
    --hidden-import tkinter `
    app.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$portable = Join-Path $projectRoot "dist\IGIModel"
Copy-Item -LiteralPath "README.md", "THIRD_PARTY_NOTICES.md", "requirements.txt" -Destination $portable -Force
Copy-Item -LiteralPath "profiles" -Destination $portable -Recurse -Force
$exampleWorkbook = Join-Path $projectRoot "outputs\Модель_грунта_пример.xlsx"
if (Test-Path -LiteralPath $exampleWorkbook) {
    $examples = Join-Path $portable "examples"
    New-Item -ItemType Directory -Path $examples -Force | Out-Null
    Copy-Item -LiteralPath $exampleWorkbook -Destination $examples -Force
}
$source = Join-Path $portable "source"
New-Item -ItemType Directory -Path $source -Force | Out-Null
Copy-Item -LiteralPath "app.py", "build_portable.ps1" -Destination $source -Force
Copy-Item -LiteralPath "igi_model" -Destination $source -Recurse -Force

Write-Host "Portable build: $portable\IGIModel.exe"
