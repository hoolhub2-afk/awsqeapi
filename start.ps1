$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$envPath = Join-Path $repoRoot ".env"
if (-not (Test-Path $envPath)) {
  Write-Error (".env not found: {0}" -f $envPath)
}

$port = $null
Get-Content $envPath | ForEach-Object {
  $line = $_.Trim()
  if (-not $line) { return }
  if ($line.StartsWith("#")) { return }

  if ($line -match "^\s*PORT\s*=\s*(.+)\s*$") {
    $raw = $Matches[1].Trim()
    if ($raw.StartsWith('"') -and $raw.EndsWith('"')) { $raw = $raw.Trim('"') }
    if ($raw.StartsWith("'") -and $raw.EndsWith("'")) { $raw = $raw.Trim("'") }
    $port = $raw
  }
}

if (-not $port) { $port = "8000" }
if ($port -notmatch "^\d+$") {
  Write-Error ("Invalid PORT in .env: {0}" -f $port)
}

$listenHost = "0.0.0.0"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Error "uv not found in PATH. Install uv, then run again."
}

Write-Host ("[start] uv run uvicorn app:app --host {0} --port {1} --reload" -f $listenHost, $port)
& uv run uvicorn app:app --host $listenHost --port $port --reload
exit $LASTEXITCODE

