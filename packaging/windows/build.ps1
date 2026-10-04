[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [string]$BuildPython = "",
    [string]$ToolRoot = (Join-Path $env:LOCALAPPDATA "Nectivon-build-tools"),
    [bool]$SigningEnabled = $false,
    [string]$SigningCertificateThumbprint = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$PackagingRoot = $PSScriptRoot
$RepositoryRoot = (Resolve-Path (Join-Path $PackagingRoot "..\..")).Path
$BuildRoot = Join-Path $PackagingRoot "build"
$CacheRoot = Join-Path $PackagingRoot "cache"
$StageRoot = Join-Path $BuildRoot "staging"
$Wheelhouse = Join-Path $BuildRoot "wheelhouse"
$DistRoot = if ($OutputDirectory) { $OutputDirectory } else { Join-Path $PackagingRoot "dist" }
$RuntimeLock = Join-Path $PackagingRoot "runtime-requirements.txt"
$PackageTools = Join-Path $PackagingRoot "scripts\package_tools.py"
$ToolchainConfig = Get-Content -LiteralPath (Join-Path $PackagingRoot "toolchain.json") -Raw |
    ConvertFrom-Json
$DotNetRoot = Join-Path $ToolRoot "dotnet"
$DotNet = Join-Path $DotNetRoot "dotnet.exe"
$Wix = Join-Path $ToolRoot "wix\wix.exe"
$MsiInspector = Join-Path $PackagingRoot "inspect-msi.ps1"
$GeneratedWix = Join-Path $BuildRoot "Files.generated.wxs"
$PythonVersion = "3.11.9"
$PythonArchive = "python-3.11.9-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonArchive"
$PythonSha256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
$BaselineSha = "670d234156a04c5f669eac4914fcbfbff7734bcb"
$VcRedistArchive = "vc_redist.x64.exe"
$VcRuntimeDlls = @(
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll"
)

function Assert-ChildPath {
    param([string]$Candidate, [string]$Parent)
    $parentPath = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $candidatePath = [System.IO.Path]::GetFullPath($Candidate)
    if (-not $candidatePath.StartsWith($parentPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe generated path outside packaging root: $candidatePath"
    }
}

function Invoke-BuildPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $script:PythonExecutable @script:PythonPrefix @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Build Python failed with exit code $LASTEXITCODE"
    }
}

function Invoke-BundledPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $script:StagePythonExecutable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Bundled Python failed with exit code $LASTEXITCODE"
    }
}

$isWindowsHost = [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
    [System.Runtime.InteropServices.OSPlatform]::Windows
)
if (-not $isWindowsHost -or -not [Environment]::Is64BitOperatingSystem) {
    throw "Nectivon Windows packaging requires 64-bit Windows."
}

$machine = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($machine -ne "X64") {
    throw "Unsupported build host architecture: $machine"
}

if (-not (Test-Path -LiteralPath $DotNet) -or -not (Test-Path -LiteralPath $Wix)) {
    throw "Isolated .NET/WiX toolchain missing. Run bootstrap-toolchain.ps1 first."
}
$DotNetVersion = (& $DotNet --version).Trim()
if ($DotNetVersion -ne $ToolchainConfig.dotnet_sdk_version) {
    throw "Expected .NET SDK $($ToolchainConfig.dotnet_sdk_version), found $DotNetVersion."
}
$env:DOTNET_ROOT = $DotNetRoot
$env:DOTNET_CLI_HOME = Join-Path $ToolRoot "dotnet-home"
$env:DOTNET_NOLOGO = "1"
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$WixVersion = (& $Wix --version).Trim()
if (-not $WixVersion.StartsWith($ToolchainConfig.wix_version)) {
    throw "Expected WiX $($ToolchainConfig.wix_version), found $WixVersion."
}

if ($BuildPython) {
    $PythonExecutable = (Get-Command $BuildPython -ErrorAction Stop).Source
    $PythonPrefix = @()
} elseif (Test-Path -LiteralPath (Join-Path $ToolRoot "python-build\Scripts\python.exe")) {
    $PythonExecutable = Join-Path $ToolRoot "python-build\Scripts\python.exe"
    $PythonPrefix = @()
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $PythonExecutable = (Get-Command py.exe).Source
    $PythonPrefix = @("-3.11")
} else {
    $PythonExecutable = (Get-Command python.exe -ErrorAction Stop).Source
    $PythonPrefix = @()
}

$BuildPythonInfo = & $PythonExecutable @PythonPrefix -c "import json,platform,struct,sys; print(json.dumps({'executable':sys.executable,'version':platform.python_version(),'bits':struct.calcsize('P')*8,'machine':platform.machine()}))"
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect build Python." }
$BuildPythonMetadata = $BuildPythonInfo | ConvertFrom-Json
if (-not $BuildPythonMetadata.version.StartsWith("3.11.") -or
    $BuildPythonMetadata.bits -ne 64 -or
    $BuildPythonMetadata.machine -notin @("AMD64", "x86_64")) {
    throw "Build Python must be CPython 3.11 x64."
}
$DevelopmentVenv = [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot ".venv"))
$ResolvedBuildPython = [System.IO.Path]::GetFullPath($BuildPythonMetadata.executable)
if ($ResolvedBuildPython.StartsWith($DevelopmentVenv, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The repository .venv is audit-only and cannot build the release runtime."
}

$CurrentSha = (git -C $RepositoryRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Unable to read source Git SHA." }
git -C $RepositoryRoot merge-base --is-ancestor $BaselineSha $CurrentSha
if ($LASTEXITCODE -ne 0) {
    throw "Source HEAD $CurrentSha does not descend from frozen v0.8.4 baseline $BaselineSha."
}
$SourceTreeDirty = [bool](git -C $RepositoryRoot status --porcelain)

Assert-ChildPath -Candidate $BuildRoot -Parent $PackagingRoot
Assert-ChildPath -Candidate $CacheRoot -Parent $PackagingRoot
if (Test-Path -LiteralPath $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $StageRoot, $Wheelhouse, $CacheRoot, $DistRoot -Force | Out-Null

$StageApp = Join-Path $StageRoot "app"
$StageScripts = Join-Path $StageRoot "scripts"
$StagePython = Join-Path $StageRoot "runtime\python"
$StagePythonExecutable = Join-Path $StagePython "python.exe"
New-Item -ItemType Directory -Path $StageApp, $StageScripts, $StagePython -Force | Out-Null

# git emits path bytes as UTF-8, but when this script's stdout is redirected
# pwsh decodes native command output with the OEM codepage and corrupts
# non-ASCII page names (CJK page files under pages/).  Round-trip the raw
# git output bytes through a temp file and decode them as UTF-8 explicitly
# so every tracked page name survives byte-exact.
$trackedListFile = Join-Path $BuildRoot "tracked-files.txt"
cmd /c "git -C $RepositoryRoot -c core.quotepath=false ls-files -- app.py pages src .streamlit/config.toml > $trackedListFile 2>nul"
if ($LASTEXITCODE -ne 0) { throw "Unable to enumerate tracked runtime files." }
$trackedRuntimeFiles = [System.IO.File]::ReadAllLines($trackedListFile, [System.Text.Encoding]::UTF8) |
    Where-Object { $_ }
if (-not $trackedRuntimeFiles) { throw "Tracked runtime file list is empty." }
foreach ($relative in $trackedRuntimeFiles) {
    $source = Join-Path $RepositoryRoot $relative
    $destination = Join-Path $StageApp $relative
    $destinationParent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination
}
Copy-Item -LiteralPath (Join-Path $RepositoryRoot "scripts\service_manager.py") -Destination $StageScripts
Copy-Item -LiteralPath (Join-Path $PackagingRoot "scripts\cleanup_user_data.py") -Destination $StageScripts
Copy-Item -LiteralPath (Join-Path $PackagingRoot "scripts\legacy_data.py") -Destination $StageScripts
Copy-Item -LiteralPath (Join-Path $PackagingRoot "scripts\uninstall_stop.py") -Destination $StageScripts
Copy-Item -LiteralPath (Join-Path $PackagingRoot "scripts\launch.vbs") -Destination $StageScripts
$VersionFilePath = Join-Path $StageRoot "VERSION"
[System.IO.File]::WriteAllText(
    $VersionFilePath,
    "0.8.5$([Environment]::NewLine)",
    (New-Object System.Text.UTF8Encoding($false))
)

$PythonArchivePath = Join-Path $CacheRoot $PythonArchive
if (-not (Test-Path -LiteralPath $PythonArchivePath)) {
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonArchivePath
}
$ActualPythonHash = (Get-FileHash -LiteralPath $PythonArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualPythonHash -ne $PythonSha256) {
    throw "CPython archive SHA-256 mismatch."
}
Expand-Archive -LiteralPath $PythonArchivePath -DestinationPath $StagePython
@("python311.zip", ".", "Lib\site-packages", "..\..\app", "import site") |
    Set-Content -LiteralPath (Join-Path $StagePython "python311._pth") -Encoding ascii

$VcRedistPath = Join-Path $CacheRoot $VcRedistArchive
if (-not (Test-Path -LiteralPath $VcRedistPath)) {
    Invoke-WebRequest -Uri $ToolchainConfig.vc_redist_x64_url -OutFile $VcRedistPath
}
$ActualVcRedistHash = (Get-FileHash -LiteralPath $VcRedistPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualVcRedistHash -ne $ToolchainConfig.vc_redist_x64_sha256) {
    throw "Visual C++ Redistributable SHA-256 mismatch."
}
$ActualVcRedistVersion = (Get-Item -LiteralPath $VcRedistPath).VersionInfo.FileVersion
if ($ActualVcRedistVersion -ne $ToolchainConfig.vc_redist_x64_file_version) {
    throw "Expected Visual C++ Redistributable $($ToolchainConfig.vc_redist_x64_file_version), found $ActualVcRedistVersion."
}
$VcBundleExtract = Join-Path $BuildRoot "vc-redist-bundle"
$VcRuntimeExtract = Join-Path $BuildRoot "vc-runtime-x64"
& $Wix burn extract $VcRedistPath -o $VcBundleExtract
if ($LASTEXITCODE -ne 0) { throw "Visual C++ Redistributable bundle extraction failed." }
$ExpandExe = Join-Path $env:SystemRoot "System32\expand.exe"
$VcCab = $null
foreach ($candidate in Get-ChildItem -LiteralPath $VcBundleExtract -File) {
    $listing = (& $ExpandExe -D $candidate.FullName 2>$null | Out-String)
    if ($LASTEXITCODE -eq 0 -and $listing -match "msvcp140\.dll_amd64") {
        $VcCab = $candidate
        break
    }
}
if ($null -eq $VcCab) { throw "The x64 Visual C++ runtime cabinet was not found." }
New-Item -ItemType Directory -Path $VcRuntimeExtract -Force | Out-Null
foreach ($dllName in $VcRuntimeDlls) {
    $archiveName = "${dllName}_amd64"
    & $ExpandExe $VcCab.FullName "-F:$archiveName" $VcRuntimeExtract | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to extract $archiveName." }
    $extracted = Join-Path $VcRuntimeExtract $archiveName
    $destination = Join-Path $StagePython $dllName
    Move-Item -LiteralPath $extracted -Destination $destination -Force
    $signature = Get-AuthenticodeSignature -LiteralPath $destination
    if ($signature.Status -ne "Valid") {
        throw "Invalid Microsoft signature on app-local runtime $dllName."
    }
}

Invoke-BuildPython -Arguments @("-m", "pip", "download", "--dest", $Wheelhouse, "--requirement", $RuntimeLock)
$SourceDistributions = Get-ChildItem -LiteralPath $Wheelhouse -File |
    Where-Object { $_.Name.EndsWith(".tar.gz") -or $_.Extension -eq ".zip" }
foreach ($sourceDistribution in $SourceDistributions) {
    Invoke-BuildPython -Arguments @(
        "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
        "--wheel-dir", $Wheelhouse, $sourceDistribution.FullName
    )
}
Invoke-BuildPython -Arguments @(
    "-m", "pip", "install", "--no-index", "--find-links", $Wheelhouse,
    "--no-deps", "--no-compile", "--only-binary", ":all:",
    "--target", (Join-Path $StagePython "Lib\site-packages"),
    "--requirement", $RuntimeLock
)
Invoke-BuildPython -Arguments @($PackageTools, "prune", $StageRoot)
$env:PYTHONDONTWRITEBYTECODE = "1"
Invoke-BundledPython -Arguments @($PackageTools, "runtime-smoke", $StageRoot)

$LockHash = (Get-FileHash -LiteralPath $RuntimeLock -Algorithm SHA256).Hash.ToLowerInvariant()
Invoke-BuildPython -Arguments @(
    $PackageTools, "manifest", $StageRoot, (Join-Path $StageRoot "runtime-manifest.json"),
    "--source-git-sha", $CurrentSha,
    "--source-tree-dirty", $SourceTreeDirty.ToString().ToLowerInvariant(),
    "--dependency-lock-sha256", $LockHash,
    "--msvc-runtime-version", $ActualVcRedistVersion,
    "--msvc-redist-sha256", $ActualVcRedistHash
)
Invoke-BuildPython -Arguments @($PackageTools, "validate", $StageRoot)
Invoke-BuildPython -Arguments @($PackageTools, "wix", $StageRoot, $GeneratedWix)

$existingListener = Get-NetTCPConnection -State Listen -LocalPort 8501 -ErrorAction SilentlyContinue
if ($existingListener) {
    $owners = $existingListener | Select-Object -ExpandProperty OwningProcess -Unique
    $ownerDetails = foreach ($ownerPid in $owners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerPid"
        "PID=$ownerPid EXE=$($process.ExecutablePath) COMMAND=$($process.CommandLine)"
    }
    Write-Output "STAGED_RUNTIME_SMOKE = BLOCKED_PORT_8501_IN_USE"
    $ownerDetails | ForEach-Object { Write-Output "PORT_8501_OWNER = $_" }
    throw "Port 8501 is occupied; refusing to stop or impersonate the existing service."
}

$SmokeRoot = Join-Path $BuildRoot "smoke-profile"
$SmokeLocalAppData = Join-Path $SmokeRoot "LocalAppData"
$SmokeAppData = Join-Path $SmokeRoot "AppData"
New-Item -ItemType Directory -Path $SmokeLocalAppData, $SmokeAppData -Force | Out-Null
$savedEnvironment = @{}
foreach ($entry in Get-ChildItem Env: | Where-Object { $_.Name -like "EKB_*" }) {
    $savedEnvironment[$entry.Name] = $entry.Value
    Remove-Item -LiteralPath "Env:$($entry.Name)"
}
$previousLocalAppData = $env:LOCALAPPDATA
$previousAppData = $env:APPDATA
$stageStarted = $false
try {
    $env:LOCALAPPDATA = $SmokeLocalAppData
    $env:APPDATA = $SmokeAppData
    $env:EKB_AI_MODE = "manual"
    $env:EKB_AI_API_KEY = ""
    $StageManager = Join-Path $StageRoot "scripts\service_manager.py"
    & $StagePythonExecutable $StageManager start --no-browser
    if ($LASTEXITCODE -ne 0) { throw "Staged service start failed: $LASTEXITCODE" }
    $stageStarted = $true
    $PidPath = Join-Path $SmokeLocalAppData "Nectivon\runtime-state\engineering-kb.pid.json"
    $PidRecord = Get-Content -LiteralPath $PidPath -Raw | ConvertFrom-Json
    $RecordedPython = [System.IO.Path]::GetFullPath([string]$PidRecord.python)
    if (-not $RecordedPython.Equals(
        [System.IO.Path]::GetFullPath($StagePythonExecutable),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Staged service did not use the bundled Python executable."
    }
    $Health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8501/_stcore/health"
    if ($Health.StatusCode -ne 200 -or $Health.Content.Trim() -ne "ok") {
        throw "Staged health endpoint failed."
    }
    $HomeResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8501/"
    if ($HomeResponse.StatusCode -ne 200) { throw "Staged home page failed." }
    Invoke-BundledPython -Arguments @($PackageTools, "app-smoke", $StageRoot)
    Write-Output "STAGED_RUNTIME_SMOKE = PASS"
} finally {
    if ($stageStarted) {
        & $StagePythonExecutable (Join-Path $StageRoot "scripts\service_manager.py") stop
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Staged service stop returned exit code $LASTEXITCODE."
        }
    }
    Remove-Item Env:EKB_AI_MODE, Env:EKB_AI_API_KEY -ErrorAction SilentlyContinue
    foreach ($name in $savedEnvironment.Keys) {
        Set-Item -LiteralPath "Env:$name" -Value $savedEnvironment[$name]
    }
    if ($null -eq $previousLocalAppData) {
        Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue
    } else {
        $env:LOCALAPPDATA = $previousLocalAppData
    }
    if ($null -eq $previousAppData) {
        Remove-Item Env:APPDATA -ErrorAction SilentlyContinue
    } else {
        $env:APPDATA = $previousAppData
    }
}

$MsiPath = Join-Path $DistRoot "Nectivon-0.8.5-windows-x64-unsigned.msi"
& $Wix build (Join-Path $PackagingRoot "wix\Package.wxs") $GeneratedWix -arch x64 -o $MsiPath
if ($LASTEXITCODE -ne 0) { throw "WiX MSI build failed." }

if ($SigningEnabled) {
    if (-not $SigningCertificateThumbprint) {
        throw "SIGNING_ENABLED requires SigningCertificateThumbprint."
    }
    $SignTool = Get-Command signtool.exe -ErrorAction Stop
    & $SignTool.Source sign /sha1 $SigningCertificateThumbprint /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $MsiPath
    if ($LASTEXITCODE -ne 0) { throw "MSI signing failed." }
} else {
    Write-Warning "UNSIGNED ENGINEERING BUILD - not for public release"
}

& $MsiInspector -MsiPath $MsiPath
if ($LASTEXITCODE -ne 0) { throw "Static MSI inspection failed." }

$MsiHash = (Get-FileHash -LiteralPath $MsiPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $DistRoot "SHA256SUMS.txt") -Value "$MsiHash  $(Split-Path -Leaf $MsiPath)" -Encoding ascii
Copy-Item -LiteralPath (Join-Path $StageRoot "runtime-manifest.json") -Destination $DistRoot
Write-Output "WINDOWS_INSTALLER_CODE_READY = YES"
Write-Output "WINDOWS_UNSIGNED_MSI_BUILD = PASS"
Write-Output "SOURCE_GIT_SHA = $CurrentSha"
Write-Output "SOURCE_TREE_DIRTY = $($SourceTreeDirty.ToString().ToLowerInvariant())"
Write-Output "DOTNET_SDK_VERSION = $DotNetVersion"
Write-Output "WIX_VERSION = $WixVersion"
Write-Output "PYTHON_VERSION = $PythonVersion"
Write-Output "PACKAGING_PIP_VERSION = $($ToolchainConfig.packaging_pip_version)"
Write-Output "PACKAGING_SETUPTOOLS_VERSION = $($ToolchainConfig.packaging_setuptools_version)"
Write-Output "PACKAGING_WHEEL_VERSION = $($ToolchainConfig.packaging_wheel_version)"
Write-Output "MSVC_RUNTIME_VERSION = $ActualVcRedistVersion"
Write-Output "MSVC_REDIST_SHA256 = $ActualVcRedistHash"
Write-Output "DEPENDENCY_LOCK_SHA256 = $LockHash"
Write-Output "MSI = $MsiPath"
Write-Output "MSI_SIZE = $((Get-Item -LiteralPath $MsiPath).Length)"
Write-Output "MSI_SHA256 = $MsiHash"
