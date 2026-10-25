param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
# Optional machine-specific paths are kept outside Git.
$configPath = Join-Path $root 'start-local.config.psd1'
$localConfig = if (Test-Path -LiteralPath $configPath) { Import-PowerShellDataFile $configPath } else { @{} }
if ($localConfig.ToolDirectories) {
    $env:PATH = ($localConfig.ToolDirectories -join ';') + ';' + $env:PATH
}
function Resolve-Executable($configured, $fallback, $command) {
    if ($configured) {
        if (!(Test-Path -LiteralPath $configured -PathType Leaf)) { throw "Executable does not exist: $configured" }
        return (Resolve-Path -LiteralPath $configured).Path
    }
    if ($fallback -and (Test-Path -LiteralPath $fallback -PathType Leaf)) { return $fallback }
    $found = Get-Command $command -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.Source }
    throw "$command is missing. See README.md for setup instructions."
}
$python = Resolve-Executable $localConfig.Python (Join-Path $root '.local-runtime\Scripts\python.exe') 'python.exe'
$node = Resolve-Executable $localConfig.Node $null 'node.exe'
$exifDirectory = Join-Path $root '.local-tools\exiftool'
$bundledExif = if (Test-Path -LiteralPath $exifDirectory) {
    Get-ChildItem -LiteralPath $exifDirectory -Recurse -Filter exiftool.exe | Select-Object -First 1
}
$configuredExif = if ($localConfig.ExifTool) { $localConfig.ExifTool } else { $env:EXIFTOOL_PATH }
$env:EXIFTOOL_PATH = Resolve-Executable $configuredExif $bundledExif.FullName 'exiftool.exe'
$env:PATH = (Split-Path -Parent $env:EXIFTOOL_PATH) + ';' + $env:PATH
$null = Resolve-Executable $null $null 'ffmpeg.exe'
$null = Resolve-Executable $null $null 'ffprobe.exe'
if (!(Test-Path -LiteralPath (Join-Path $root 'frontend\node_modules\vite\bin\vite.js'))) {
    throw 'Frontend dependencies are missing. Run npm ci in frontend first.'
}
function Start-ServiceLocal($name, $port, $exe, $arguments, $directory, $health) {
    try { $null = Invoke-WebRequest $health -UseBasicParsing -TimeoutSec 2; Write-Host "$name already running on $port"; return } catch {}
    $process = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $directory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $root "$name-local.out.log") -RedirectStandardError (Join-Path $root "$name-local.err.log")
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) { throw "$name exited. See $name-local.err.log" }
        try { $null = Invoke-WebRequest $health -UseBasicParsing -TimeoutSec 2; Write-Host "$name ready on $port (PID $($process.Id))"; return } catch {}
    }
    throw "$name did not become ready. See $name-local.err.log"
}
Start-ServiceLocal 'backend' 8000 $python '-m uvicorn app.main:app --host 127.0.0.1 --port 8000' (Join-Path $root 'backend') 'http://127.0.0.1:8000/api/health'
Start-ServiceLocal 'labeling' 8002 $python '-m uvicorn app.main:app --host 127.0.0.1 --port 8002' (Join-Path $root 'labeling-backend') 'http://127.0.0.1:8002/api/v1/health'
Start-ServiceLocal 'frontend' 5173 $node 'node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort' (Join-Path $root 'frontend') 'http://127.0.0.1:5173/'
if (!$NoBrowser) { Start-Process 'http://127.0.0.1:5173/' }
