$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
$env:PATH = (Join-Path $projectRoot '.venv/Scripts') + ';' + $env:PATH
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = Join-Path $projectRoot '.local/models'
$pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
$ollamaExe = Join-Path $projectRoot '.local/ollama/ollama.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Create .venv and install the project dependencies first; see README.'
}
try { $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 }
catch {
    if (-not (Test-Path -LiteralPath $ollamaExe)) { throw 'Ollama is not running; install it or restore .local/ollama.' }
    Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $projectRoot '.local/ollama.stdout.log') `
        -RedirectStandardError (Join-Path $projectRoot '.local/ollama.stderr.log')
}
Write-Host 'Demo: http://127.0.0.1:8765 — press Ctrl+C to stop the web server.'
& $pythonExe -m uvicorn enrolment.demo.app:create_app --factory --host 127.0.0.1 --port 8765
