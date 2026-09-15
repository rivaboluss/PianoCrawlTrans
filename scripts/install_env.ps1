# Piano Transcriber environment installer (Windows)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install_env.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Ensure-Dir($p) {
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

function Get-Uv {
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "uv\uv.exe"),
        "uv"
    )
    foreach ($c in $candidates) {
        if ($c -eq "uv") {
            $cmd = Get-Command uv -ErrorAction SilentlyContinue
            if ($cmd) { return $cmd.Source }
        } elseif (Test-Path $c) {
            return $c
        }
    }
    return $null
}

Write-Host "Piano Transcriber - environment installer" -ForegroundColor Yellow
Write-Host "Root: $Root"

# ---- prerequisites ----
Write-Step "Check NVIDIA / CUDA"
try {
    $gpu = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null
    if ($LASTEXITCODE -ne 0) { throw "nvidia-smi failed" }
    Write-Host "GPU: $gpu"
} catch {
    Write-Host "WARNING: NVIDIA driver not detected. GPU transcription will be unavailable." -ForegroundColor Red
}

Write-Step "Install or locate uv"
$uv = Get-Uv
if (-not $uv) {
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$(Join-Path $env:USERPROFILE '.local\bin');$env:Path"
    $uv = Get-Uv
}
if (-not $uv) { throw "uv installation failed. Install it manually: https://docs.astral.sh/uv/" }
Write-Host "uv: $uv"
$env:Path = "$(Split-Path $uv -Parent);$env:Path"
# Copy mode is more reliable across drives and avoids hardlink fallback stalls.
$env:UV_LINK_MODE = "copy"

Ensure-Dir (Join-Path $Root "musicdata")
Ensure-Dir (Join-Path $Root "output")

# ---- GUI (Flask) ----
Write-Step "Install GUI dependencies (runtime\.venv)"
$runtime = Join-Path $Root "runtime"
Ensure-Dir $runtime
& $uv venv (Join-Path $runtime ".venv") --python 3.12 --clear
$guiPy = Join-Path $runtime ".venv\Scripts\python.exe"
& $uv pip install --python $guiPy -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Flask dependency installation failed" }

# ---- Aria-AMT ----
Write-Step "Install Aria-AMT (local Aria venv)"
$aria = Get-ChildItem -LiteralPath $Root -Directory | Where-Object {
    (Test-Path (Join-Path $_.FullName "checkpoints")) -and
    (Test-Path (Join-Path $_.FullName "amt"))
} | Select-Object -First 1 -ExpandProperty FullName
if (-not $aria) { throw "Cannot find Aria-AMT project directory" }
$ariaVenv = Join-Path $aria ".venv"
$ckpt = Join-Path $aria "checkpoints\piano-medium-double-1.0.safetensors"
if (-not (Test-Path $ckpt)) {
    Write-Host "Missing Aria checkpoint: $ckpt" -ForegroundColor Red
    Write-Host "Put piano-medium-double-1.0.safetensors in the checkpoints folder." -ForegroundColor Red
    throw "Aria checkpoint missing"
}
$utils = Join-Path $aria "third_party\aria-utils-main"
if (-not (Test-Path $utils)) {
    throw "Missing ariautils source: $utils"
}

& $uv venv $ariaVenv --python 3.12 --clear
$ariaPy = Join-Path $ariaVenv "Scripts\python.exe"
$env:UV_TORCH_BACKEND = "cu126"
& $uv pip install --python $ariaPy "torch" "torchaudio"
if ($LASTEXITCODE -ne 0) { throw "Aria torch installation failed" }
& $uv pip install --python $ariaPy safetensors librosa tqdm orjson setuptools wheel mido
Push-Location $aria
try {
    & $uv pip install --python $ariaPy -e $utils --no-deps
    & $uv pip install --python $ariaPy -e . --no-deps
} finally {
    Pop-Location
}
if (-not (Test-Path (Join-Path $aria "transcribe_local.py"))) {
    throw "Missing transcribe_local.py"
}
& $ariaPy -c "import torch, amt, ariautils; assert torch.cuda.is_available(), 'CUDA unavailable'; print('Aria OK', torch.__version__, torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) { throw "Aria self-check failed" }

# ---- MuScriptor ----
Write-Step "Install MuScriptor (local MuScriptor venv)"
$ms = Get-ChildItem -LiteralPath $Root -Directory | Where-Object {
    (Test-Path (Join-Path $_.FullName "transcribe_local.py")) -and
    -not (Test-Path (Join-Path $_.FullName "amt"))
} | Select-Object -First 1 -ExpandProperty FullName
if (-not $ms) { throw "Cannot find MuScriptor project directory" }
Ensure-Dir $ms
$msVenv = Join-Path $ms ".venv"
& $uv venv $msVenv --python 3.12 --clear
$msPy = Join-Path $msVenv "Scripts\python.exe"
$env:UV_TORCH_BACKEND = "cu126"
& $uv pip install --python $msPy muscriptor
if ($LASTEXITCODE -ne 0) { throw "MuScriptor installation failed" }
if (-not (Test-Path (Join-Path $ms "transcribe_local.py"))) {
    throw "Missing MuScriptor transcribe_local.py"
}
& $msPy -c "import torch, muscriptor; assert torch.cuda.is_available(), 'CUDA unavailable'; print('MuScriptor OK', torch.__version__, torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) { throw "MuScriptor self-check failed" }

# ---- .env ----
Write-Step "Prepare .env"
$envFile = Join-Path $Root ".env"
$envExample = Join-Path $Root ".env.example"
if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Host "Created .env. Fill in HF_TOKEN to use MuScriptor." -ForegroundColor Yellow
    }
} else {
    Write-Host ".env already exists; skipping."
}

# ---- marker ----
$marker = Join-Path $Root "runtime\.installed"
"installed=$(Get-Date -Format o)" | Set-Content -Encoding UTF8 $marker

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host "1) Edit .env and add a HuggingFace token (required by MuScriptor)"
Write-Host "2) Run start.bat"
Write-Host "3) Open http://127.0.0.1:8765 in a browser"
