$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw '프로젝트 .venv가 없습니다. Python 3.12 이상으로 python -m venv .venv 후 requirements.txt를 설치해 주세요.'
}
& $projectPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
