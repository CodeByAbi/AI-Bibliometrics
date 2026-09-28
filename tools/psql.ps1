# psql ke DB_URL tanpa install psql native (pakai image postgres:16-alpine lokal).
#
# Usage:
#     .\tools\psql.ps1                                  # interactive psql
#     .\tools\psql.ps1 -c "SELECT COUNT(*) FROM chunks;" # single command
#     .\tools\psql.ps1 -c "\dt"                          # list tables
#
# .env di-load otomatis; sslmode=require ditambahkan otomatis.

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PsqlArgs
)

. (Join-Path $PSScriptRoot "Load-Env.ps1")

if ([string]::IsNullOrEmpty($env:DB_URL)) {
    Write-Error "DB_URL kosong setelah load .env."
    exit 1
}

$dsn = $env:DB_URL + "?sslmode=require"

# Allocate a TTY only for real interactive sessions; single commands (-c/-f)
# and non-console hosts (CI, piped input) run without one.
$interactive = $true
foreach ($a in $PsqlArgs) {
    if ($a -match "^(-c|--command|-f|--file|-l|--list)$") { $interactive = $false; break }
}
$tty = @()
if ($interactive -and -not [Console]::IsInputRedirected) { $tty = @("-it") }

docker run --rm @tty postgres:16-alpine psql "$dsn" @PsqlArgs
