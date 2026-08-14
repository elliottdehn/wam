[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepositoryRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VirtualEnvironment = Join-Path $RepositoryRoot '.venv'
$VirtualPython = Join-Path $VirtualEnvironment 'Scripts\python.exe'

# Prefer the Python launcher because the Microsoft Store alias can claim to be
# Python while opening an installer instead of executing the requested command.
# `py -3` selects an installed Python 3 without pinning setup to exactly 3.11.
if (-not (Test-Path -LiteralPath $VirtualPython)) {
    $BasePython = $null
    $BaseArguments = @()
    $Launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $Launcher) {
        & $Launcher.Source -3 -c "import sys; raise SystemExit(sys.version_info < (3, 9))" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $BasePython = $Launcher.Source
            $BaseArguments = @('-3')
        }
    }

    if ($null -eq $BasePython) {
        $Python = Get-Command python -ErrorAction Stop
        & $Python.Source -c "import sys; raise SystemExit(sys.version_info < (3, 9))"
        if ($LASTEXITCODE -ne 0) {
            throw "WAM requires Python 3.9 or newer"
        }
        $BasePython = $Python.Source
    }

    & $BasePython @BaseArguments -m venv $VirtualEnvironment
}

if (-not (Test-Path -LiteralPath $VirtualPython)) {
    throw "The virtual environment did not create $VirtualPython"
}

# Python 3.9 bundles pip 21.2, which predates PEP 660 and refuses an editable
# install from a pyproject-only project. Upgrading first keeps the supported
# floor installable rather than merely declared.
& $VirtualPython -m pip install --quiet --upgrade "pip>=21.3" setuptools wheel
if ($LASTEXITCODE -ne 0) {
    throw "WAM could not upgrade pip with exit code $LASTEXITCODE"
}

# Editable installation keeps the CLI bound to this checkout so Codex always
# runs the exact source files it is reviewing and modifying.
& $VirtualPython -m pip install --editable $RepositoryRoot
if ($LASTEXITCODE -ne 0) {
    throw "WAM dependency installation failed with exit code $LASTEXITCODE"
}

& $VirtualPython -m wam.codex_cli --help
if ($LASTEXITCODE -ne 0) {
    throw "WAM validation failed with exit code $LASTEXITCODE"
}

Write-Host "WAM is ready: $VirtualPython"
