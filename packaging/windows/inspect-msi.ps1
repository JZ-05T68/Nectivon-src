[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$MsiPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$resolved = (Resolve-Path -LiteralPath $MsiPath).Path
$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $installer.OpenDatabase($resolved, 0)

function Read-MsiRows {
    param([string]$Query, [int]$FieldCount)
    $view = $database.OpenView($Query)
    [void]$view.Execute()
    $rows = [System.Collections.Generic.List[object]]::new()
    while ($record = $view.Fetch()) {
        [object[]]$values = @(for ($index = 1; $index -le $FieldCount; $index++) {
            $record.StringData($index)
        })
        $rows.Add([pscustomobject]@{ Values = $values })
    }
    [void]$view.Close()
    return $rows
}

$properties = @{}
foreach ($row in (Read-MsiRows -Query 'SELECT `Property`, `Value` FROM `Property`' -FieldCount 2)) {
    $properties[$row.Values[0]] = $row.Values[1]
}
if ($properties.ProductName -ne "Nectivon") { throw "Unexpected ProductName." }
if ($properties.ProductVersion -ne "0.8.5") { throw "Unexpected ProductVersion." }
if ($properties.UpgradeCode -ne "{76EF2892-AB92-4BC3-8F22-D4EA00C55B5B}") {
    throw "Unexpected UpgradeCode."
}
if ($properties.ContainsKey("ALLUSERS")) { throw "ALLUSERS must be absent for a per-user MSI." }

$directories = Read-MsiRows -Query 'SELECT `Directory`, `Directory_Parent`, `DefaultDir` FROM `Directory`' -FieldCount 3
$directoryText = ($directories | ForEach-Object { $_.Values -join "|" }) -join "`n"
if ($directoryText -notmatch "LocalAppDataFolder" -or
    $directoryText -notmatch "Programs" -or
    $directoryText -notmatch "Nectivon") {
    throw "Per-user LocalAppData Programs/Nectivon directory contract missing."
}
$shortcuts = Read-MsiRows -Query 'SELECT `Name`, `Target`, `Arguments` FROM `Shortcut`' -FieldCount 3
$shortcutText = ($shortcuts | ForEach-Object { $_.Values -join "|" }) -join "`n"
if ($shortcutText -notmatch "Nectivon" -or $shortcutText -notmatch "wscript.exe") {
    throw "Nectivon Start Menu shortcut contract missing."
}
$tables = Read-MsiRows -Query 'SELECT `Name` FROM `_Tables`' -FieldCount 1 | ForEach-Object { $_.Values[0] }
if ($tables -notcontains "CustomAction") { throw "Uninstall lifecycle actions are missing." }
$customActions = Read-MsiRows -Query 'SELECT `Action`, `Type`, `Source`, `Target` FROM `CustomAction`' -FieldCount 4
$byAction = @{}
foreach ($row in $customActions) { $byAction[$row.Values[0]] = $row.Values }
if ($byAction.Count -ne 2) { throw "Exactly the two constrained custom actions are required." }
$autostartAction = $byAction["RemoveNectivonAutostart"]
if ($null -eq $autostartAction -or
    $autostartAction[2] -ne "SystemFolder" -or
    $autostartAction[3] -ne 'reg.exe delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v Nectivon /f') {
    throw "Unexpected autostart custom action content."
}
$stopAction = $byAction["StopNectivonRuntimeBeforeRemoval"]
if ($null -eq $stopAction -or
    $stopAction[3] -notmatch 'uninstall_stop\.py' -or
    $stopAction[3] -notmatch 'runtime\\python\\python\.exe') {
    throw "Uninstall stop helper action is missing or malformed."
}
$sequenceRows = Read-MsiRows -Query 'SELECT `Action`, `Condition`, `Sequence` FROM `InstallExecuteSequence` WHERE `Action` = ''RemoveNectivonAutostart''' -FieldCount 3
if ($sequenceRows.Count -ne 1 -or $sequenceRows[0].Values[1] -ne 'REMOVE~="ALL"') {
    throw "Autostart cleanup must run only for a full uninstall."
}
$stopSequenceRows = Read-MsiRows -Query 'SELECT `Action`, `Condition`, `Sequence` FROM `InstallExecuteSequence` WHERE `Action` = ''StopNectivonRuntimeBeforeRemoval''' -FieldCount 3
if ($stopSequenceRows.Count -ne 1 -or $stopSequenceRows[0].Values[1] -ne 'REMOVE~="ALL"') {
    throw "Uninstall stop helper must run only for a full uninstall."
}
if ($tables -contains "RemoveFile") {
    $removeRows = Read-MsiRows -Query 'SELECT `FileName`, `DirProperty`, `InstallMode` FROM `RemoveFile`' -FieldCount 3
    $removeText = ($removeRows | ForEach-Object { $_.Values -join "|" }) -join "`n"
    if ($removeText -match "data|config|backup|credential|knowledge") {
        throw "Uninstall removal table references user-owned data."
    }
}

$summary = $database.SummaryInformation(0)
$template = $summary.Property(7)
if ($template -notmatch "x64") { throw "MSI summary does not declare x64." }

Write-Output "WINDOWS_UNSIGNED_MSI_STATIC_INSPECTION = PASS"
Write-Output "PRODUCT_NAME = $($properties.ProductName)"
Write-Output "PRODUCT_VERSION = $($properties.ProductVersion)"
Write-Output "UPGRADE_CODE = $($properties.UpgradeCode)"
Write-Output "MSI_TEMPLATE = $template"
Write-Output "INSTALL_SCOPE = perUser"
Write-Output "INSTALL_DIRECTORY = %LOCALAPPDATA%\Programs\Nectivon"
Write-Output "AUTOSTART_UNINSTALL_CLEANUP = HKCU Run\\Nectivon only"
