# Build release package into dist\PianoTranscriber-vX\
# Usage: powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$Root = Split-Path -Parent $ScriptDir

$VersionFile = Join-Path $Root "VERSION"
$Version = "1.0.0"
if (Test-Path $VersionFile) {
    $Version = (Get-Content $VersionFile -Encoding UTF8 | Select-Object -First 1).Trim()
}

$DistRoot = Join-Path $Root "dist"
$OutName = "PianoTranscriber-v$Version"
$OutDir = Join-Path $DistRoot $OutName
$ZipPath = Join-Path $DistRoot "$OutName.zip"

Write-Host "Building release -> $OutDir"

if (Test-Path $DistRoot) {
    Get-ChildItem $DistRoot -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

$AriaDir = $null
$MsDir = $null
$YtDir = $null
$HfModelName = "models--MuScriptor--muscriptor-small"
$HfModelSource = Join-Path $env:USERPROFILE ".cache\huggingface\hub\$HfModelName"
Get-ChildItem -LiteralPath $Root -Directory -Force | ForEach-Object {
    $p = $_.FullName
    if ((Test-Path (Join-Path $p "yt.exe")) -and (-not $YtDir)) { $YtDir = $_ }
    if ((Test-Path (Join-Path $p "transcribe_local.py")) -and (Test-Path (Join-Path $p "amt")) -and (-not $AriaDir)) {
        $AriaDir = $_
    }
    if ((Test-Path (Join-Path $p "transcribe_local.py")) -and (-not (Test-Path (Join-Path $p "amt"))) -and (-not $MsDir)) {
        $MsDir = $_
    }
}

if (-not $AriaDir) { throw "Cannot find aria-amt project folder under $Root" }
if (-not $MsDir) { throw "Cannot find muscriptor folder under $Root" }
if (-not $YtDir) { throw "Cannot find yt-dlp folder under $Root" }
if (-not (Test-Path -LiteralPath $HfModelSource)) {
    throw "Cannot find cached MuScriptor model: $HfModelSource"
}

Write-Host "Aria      : $($AriaDir.Name)"
Write-Host "MuScriptor: $($MsDir.Name)"
Write-Host "yt-dlp    : $($YtDir.Name)"
Write-Host "MuScriptor model: $HfModelSource"

function Robo-Copy([string]$src, [string]$dst, [string[]]$xd = @()) {
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    $ra = @($src, $dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/nc", "/ns", "/np")
    foreach ($d in $xd) { $ra += @("/XD", $d) }
    & robocopy @ra | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $src -> $dst (code $LASTEXITCODE)" }
}

foreach ($f in @(
    "app.py", "requirements.txt", "VERSION", "README.md",
    ".env.example", ".gitignore", "start.bat"
)) {
    $p = Join-Path $Root $f
    if (Test-Path -LiteralPath $p) {
        Copy-Item -LiteralPath $p -Destination (Join-Path $OutDir $f) -Force
    }
}

Get-ChildItem -LiteralPath $Root -File -Force | ForEach-Object {
    if ($_.Extension -eq ".bat" -or $_.Extension -eq ".md") {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $OutDir $_.Name) -Force
    }
}

Robo-Copy (Join-Path $Root "web") (Join-Path $OutDir "web")
Robo-Copy (Join-Path $Root "scripts") (Join-Path $OutDir "scripts")
Robo-Copy $YtDir.FullName (Join-Path $OutDir $YtDir.Name)

$ariaOut = Join-Path $OutDir $AriaDir.Name
Robo-Copy $AriaDir.FullName $ariaOut @(
    ".venv", ".git", ".github", "__pycache__", "aria_amt.egg-info",
    "input", "output", "tests", "scripts", "wheels"
)

Get-ChildItem -LiteralPath $ariaOut -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -eq ".zip" } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }

$msOut = Join-Path $OutDir $MsDir.Name
New-Item -ItemType Directory -Force -Path $msOut | Out-Null
foreach ($f in @("transcribe_local.py", "README.md")) {
    $p = Join-Path $MsDir.FullName $f
    if (Test-Path -LiteralPath $p) {
        Copy-Item -LiteralPath $p -Destination (Join-Path $msOut $f) -Force
    }
}

$hfOut = Join-Path $OutDir "huggingface\hub\$HfModelName"
Robo-Copy $HfModelSource $hfOut

New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "musicdata") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "output") | Out-Null
"" | Set-Content -Encoding ASCII (Join-Path $OutDir "musicdata\.gitkeep")
"" | Set-Content -Encoding ASCII (Join-Path $OutDir "output\.gitkeep")

Remove-Item -LiteralPath (Join-Path $OutDir ".env") -Force -ErrorAction SilentlyContinue

$sizeMb = [math]::Round(((Get-ChildItem -LiteralPath $OutDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "Folder size: $sizeMb MB"

if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }

Push-Location $DistRoot
try {
    & tar.exe -a -c -f "${OutName}.zip" $OutName
    if ($LASTEXITCODE -ne 0) { throw "tar failed with code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$zipMb = [math]::Round(((Get-Item -LiteralPath $ZipPath).Length / 1MB), 1)
Write-Host "Done: $ZipPath ($zipMb MB)"
Write-Host "Share the zip. Recipients: unzip -> install bat -> edit .env -> start bat"
