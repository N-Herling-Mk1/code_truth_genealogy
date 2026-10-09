# first_push.ps1 -- run from the folder you extracted the drop into (the repo root).
#   Unblock-File .\first_push.ps1 ; .\first_push.ps1
$ErrorActionPreference = "Stop"
Write-Host "[1/6] unblocking delivered files"; Get-ChildItem -Recurse -File | Unblock-File
Write-Host "[2/6] build"; python tools\build.py
if ($LASTEXITCODE -ne 0) { throw "build.py exited $LASTEXITCODE -- fix before pushing" }
Write-Host "[3/6] git init"
if (-not (Test-Path .git)) { git init -b main | Out-Null; git remote add origin https://github.com/N-Herling-Mk1/code_truth_genealogy.git }
git config core.autocrlf false
Write-Host "[4/6] stage"; git add -A
Write-Host "[5/6] commit"; git commit -m "Initial site: schema, build + seal tools, front end, four DRAFT units"
Write-Host "[6/6] push"; git push -u origin main
Write-Host "done. Now: repo Settings -> Pages -> Deploy from a branch -> main /docs"
