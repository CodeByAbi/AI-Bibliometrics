# Load project .env into the current PowerShell session (process scope only).
#
# Usage (from repo root):
#     . .\tools\Load-Env.ps1
#
# Must be dot-sourced (note the leading dot + space) so variables persist
# in your session. Nothing secret is printed — only a redacted label.
# .env stays untracked (see .gitignore); values live in memory only.

$envFile = Join-Path $PSScriptRoot "..\\.env"
$envFile = [System.IO.Path]::GetFullPath($envFile)

if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Error "No .env found at $envFile. Copy .env.example to .env first."
    return
}

$count = 0
Get-Content -LiteralPath $envFile | ForEach-Object {
    $line = $_.Trim()
    if ([string]::IsNullOrWhiteSpace($line)) { return }
    if ($line.StartsWith("#")) { return }
    if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $key = $line.Substring(0, $idx).Trim()
    $val = $line.Substring($idx + 1).Trim()
    # Strip one layer of matching surrounding quotes, if present.
    if ($val.Length -ge 2) {
        $first = $val[0]
        $last = $val[$val.Length - 1]
        if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
            $val = $val.Substring(1, $val.Length - 2)
        }
    }
    if ([string]::IsNullOrEmpty($key)) { return }
    [Environment]::SetEnvironmentVariable($key, $val, "Process")
    $count++
}

$label = "<unset>"
if (-not [string]::IsNullOrEmpty($env:DB_URL)) {
    $label = $env:DB_URL -replace "://([^:/@]+):[^@]+@", '://$1@'
}

Write-Host "Loaded $count vars from .env -> DB_URL: $label"
