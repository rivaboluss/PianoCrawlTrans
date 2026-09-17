param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Audio,
    [string]$SaveDir = "output",
    [switch]$NoSilenceFilter,
    [double]$SilenceTopDb = 45,
    [switch]$Normalize
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$FfmpegDir = Join-Path $Root "tools\ffmpeg"

if (-not (Test-Path $Python)) {
    Write-Error "venv python not found: $Python"
}

if (Test-Path $FfmpegDir) {
    $env:Path = "$FfmpegDir;$env:Path"
}

Set-Location $Root
$pyArgs = @(
    (Join-Path $Root "transcribe_local.py"),
    $Audio,
    "-o", $SaveDir,
    "--silence-top-db", "$SilenceTopDb"
)
if ($NoSilenceFilter) { $pyArgs += "--no-silence-filter" }
if ($Normalize) { $pyArgs += "--normalize" }

& $Python @pyArgs
exit $LASTEXITCODE
