param(
    [string]$CondaEnv = 'uipath-test',
    [string]$Python,
    [ValidateRange(1, 65535)][int]$Port = 5000,
    [switch]$CheckOnly,
    [switch]$SaveLocalConfig,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'crm_launcher.py'
$arguments = @($launcher, 'start', '--port', $Port)
if ($CheckOnly) { $arguments += '--check-only' }
if ($SaveLocalConfig) { $arguments += '--save-local-config' }
if ($NoBrowser) { $arguments += '--no-browser' }

if ($Python) {
    & $Python @arguments
} else {
    $conda = $env:CONDA_EXE
    if (-not $conda) {
        $conda = (Get-Command conda.exe -ErrorAction SilentlyContinue).Source
    }
    if (-not $conda) {
        foreach ($homeName in @('anaconda3', 'miniconda3', 'miniforge3')) {
            $candidate = Join-Path $env:USERPROFILE "$homeName/Scripts/conda.exe"
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $conda = $candidate
                break
            }
        }
    }
    if (-not $conda) { throw 'Conda was not found. Set CONDA_EXE or supply -Python.' }
    & $conda run --no-capture-output -n $CondaEnv python @arguments
}
exit $LASTEXITCODE
