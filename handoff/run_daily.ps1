# Daily Gaiapus snapshot (Windows). Schedule with Task Scheduler after 06:00 UTC.
# Needs EARTHDATA_TOKEN in the user environment and the Python environment from requirements.txt.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONPATH = $PSScriptRoot
if (-not $env:EARTHDATA_TOKEN) { $env:EARTHDATA_TOKEN = [Environment]::GetEnvironmentVariable('EARTHDATA_TOKEN', 'User') }
$python = if ($env:GAIAPUS_PYTHON) { $env:GAIAPUS_PYTHON } else { 'python' }
$log = Join-Path $PSScriptRoot ("logs\daily_{0:yyyy-MM-dd}.log" -f (Get-Date))
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
& $python -u -m data.serving.daily_job --mode live --out dist/snapshots_live --workers 6 *>> $log
exit $LASTEXITCODE
