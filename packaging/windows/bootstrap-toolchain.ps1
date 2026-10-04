[CmdletBinding()]
param(
    [string]$ToolRoot = (Join-Path $env:LOCALAPPDATA "Nectivon-build-tools"),
    [string]$BasePython = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot "toolchain.json") -Raw |
    ConvertFrom-Json
$root = [System.IO.Path]::GetFullPath($ToolRoot)
$downloads = Join-Path $root "downloads"
$dotnetRoot = Join-Path $root "dotnet"
$wixRoot = Join-Path $root "wix"
$buildPythonRoot = Join-Path $root "python-build"
$installer = Join-Path $downloads "dotnet-install.ps1"
New-Item -ItemType Directory -Path $downloads, $dotnetRoot, $wixRoot -Force | Out-Null

Invoke-WebRequest -UseBasicParsing -Uri $config.dotnet_install_script_url -OutFile $installer
$installerHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($installerHash -ne $config.dotnet_install_script_sha256) {
    throw "dotnet-install.ps1 SHA-256 mismatch; review the upstream script before updating toolchain.json."
}

$dotnet = Join-Path $dotnetRoot "dotnet.exe"
if (-not (Test-Path -LiteralPath $dotnet)) {
    & $installer -Version $config.dotnet_sdk_version -Architecture x64 `
        -InstallDir $dotnetRoot -NoPath
}
if (-not (Test-Path -LiteralPath $dotnet)) {
    throw "The isolated .NET installation did not produce dotnet.exe."
}
$dotnetVersion = (& $dotnet --version).Trim()
if ($dotnetVersion -ne $config.dotnet_sdk_version) {
    throw "Expected .NET SDK $($config.dotnet_sdk_version), found $dotnetVersion."
}

$env:DOTNET_ROOT = $dotnetRoot
$env:DOTNET_CLI_HOME = Join-Path $root "dotnet-home"
$env:DOTNET_NOLOGO = "1"
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$wix = Join-Path $wixRoot "wix.exe"
if (Test-Path -LiteralPath $wix) {
    & $dotnet tool update --tool-path $wixRoot wix --version $config.wix_version
} else {
    & $dotnet tool install --tool-path $wixRoot wix --version $config.wix_version
}
if ($LASTEXITCODE -ne 0) {
    throw "WiX tool installation failed with exit code $LASTEXITCODE."
}
$wixVersion = (& $wix --version).Trim()
if (-not $wixVersion.StartsWith($config.wix_version)) {
    throw "Expected WiX $($config.wix_version), found $wixVersion."
}

Write-Output "WINDOWS_TOOLCHAIN_BOOTSTRAP = PASS"
Write-Output "DOTNET_SDK_VERSION = $dotnetVersion"
Write-Output "WIX_VERSION = $wixVersion"
Write-Output "TOOL_ROOT = $root"

if ($BasePython) {
    $basePythonExecutable = (Get-Command $BasePython -ErrorAction Stop).Source
    $buildPython = Join-Path $buildPythonRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $buildPython)) {
        & $basePythonExecutable -m venv $buildPythonRoot
        if ($LASTEXITCODE -ne 0) { throw "Packaging Python venv creation failed." }
    }
    & $buildPython -m pip install --disable-pip-version-check `
        "pip==$($config.packaging_pip_version)" `
        "setuptools==$($config.packaging_setuptools_version)" `
        "wheel==$($config.packaging_wheel_version)"
    if ($LASTEXITCODE -ne 0) { throw "Packaging Python tool installation failed." }
    Write-Output "PACKAGING_PYTHON = $buildPython"
    & $buildPython -m pip --version
}
