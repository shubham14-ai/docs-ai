# Document Insights API CLI — PowerShell wrapper around doc-ai.sh
#
# Windows cannot run a .sh directly from PowerShell (the .sh association opens a
# throwaway Git Bash window, and `bash` on PATH is often WSL). This wrapper finds
# Git's bundled bash and forwards every argument to doc-ai.sh.
#
#   .\doc-ai.ps1 init
#   .\doc-ai.ps1 logs worker
#   MODE is set via env:  $env:MODE='prod'; .\doc-ai.ps1 up

$ErrorActionPreference = 'Stop'

# Locate Git's bash (NOT wsl's). Prefer git's own install dir, then common paths.
$bash = $null
$candidates = @(
    "$env:ProgramFiles\Git\bin\bash.exe",
    "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
    "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
)
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($gitCmd) {
    # e.g. C:\Program Files\Git\cmd\git.exe -> C:\Program Files\Git\bin\bash.exe
    $gitRoot = Split-Path (Split-Path $gitCmd.Source -Parent) -Parent
    $candidates = @("$gitRoot\bin\bash.exe") + $candidates
}
foreach ($c in $candidates) {
    if (Test-Path $c) { $bash = $c; break }
}
if (-not $bash) {
    Write-Error "Git Bash not found. Install Git for Windows, or run doc-ai.sh from a Git Bash terminal."
    exit 1
}

$script = Join-Path $PSScriptRoot 'doc-ai.sh'
& $bash $script @args
exit $LASTEXITCODE
