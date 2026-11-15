# Daily live Gaiapus snapshot on this research machine (registered in Task Scheduler as "Gaiapus daily live job").
# Also seals the v9 prospective registry: only data/prospective_registry is committed and pushed (never forced).
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$env:PYTHONPATH = $root
$env:EARTHDATA_TOKEN = [Environment]::GetEnvironmentVariable('EARTHDATA_TOKEN', 'User')
$python = Join-Path $env:USERPROFILE '.venvs\gaiapus\Scripts\python.exe'
$log = Join-Path $root ("logs\daily_live_{0:yyyy-MM-dd}.log" -f (Get-Date))
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
& $python -u -W ignore -m data.serving.daily_job --mode live --out dist/snapshots_live --workers 6 *>> $log
$code = $LASTEXITCODE
& $python -u -W ignore -m data.serving.prospective_registry verify *>> $log
if ($LASTEXITCODE -eq 0 -and (Test-Path (Join-Path $root 'data\prospective_registry'))) {
    $ErrorActionPreference = 'Continue'
    git add -- data/prospective_registry *>> $log
    git commit -m ("Prospective registry {0:yyyy-MM-dd}" -f (Get-Date)) -- data/prospective_registry *>> $log
    git push *>> $log
}
exit $code
