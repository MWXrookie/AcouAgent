[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [string]$OutputDirectory = 'outputs/desktop-installer-smoke'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$outputRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$workRoot = Join-Path $tempRoot ('acouagent-desktop-installer-' + [guid]::NewGuid().ToString('N'))
$installRoot = Join-Path $workRoot 'application'
$profileRoot = Join-Path $env:LOCALAPPDATA 'AcouAgent\Desktop'
if (Test-Path -LiteralPath $profileRoot) { throw 'existing default desktop user data found; refuse to use it for disposable installer tests' }
if (Test-Path -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'AcouAgent.lnk')) {
    throw 'existing AcouAgent desktop shortcut found; refuse to replace another installation'
}
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
if (Get-ChildItem -LiteralPath $uninstallKey -ErrorAction SilentlyContinue | Get-ItemProperty | Where-Object { $_.DisplayName -eq 'AcouAgent' }) {
    throw 'existing AcouAgent installation found; use a disposable Windows account for installer acceptance'
}
New-Item -ItemType Directory -Path $workRoot,$outputRoot -Force | Out-Null
$checks = @()
function Run-Installer {
    $process = Start-Process -FilePath $installerPath -ArgumentList @('/S',('/D=' + $installRoot)) -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw ('installer exit ' + $process.ExitCode) }
    if (-not (Test-Path -LiteralPath (Join-Path $installRoot 'AcouAgent.exe'))) { throw 'installed executable missing' }
}
try {
    Run-Installer
    $checks += 'per_user_install_success'
    $env:ACOUAGENT_ELECTRON_EXE = Join-Path $installRoot 'AcouAgent.exe'
    $env:ACOUAGENT_DESKTOP_SMOKE_OUTPUT = Join-Path $outputRoot 'installed-window'
    $env:ACOUAGENT_DESKTOP_SMOKE_DEFAULT_PROFILE = 'true'
    & node (Join-Path $PSScriptRoot 'smoke.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'installed Electron window smoke failed' }
    $checks += 'installed_application_window_smoke'
    $qaProfile = $profileRoot
    $vaultPath = Join-Path $qaProfile 'credentials.bin'
    $vaultHash = (Get-FileHash -LiteralPath $vaultPath -Algorithm SHA256).Hash
    $marker = Join-Path $qaProfile 'preserve-on-upgrade.txt'
    [IO.File]::WriteAllText($marker, 'owned test marker')
    Run-Installer
    if ((Get-FileHash -LiteralPath $vaultPath -Algorithm SHA256).Hash -ne $vaultHash -or -not (Test-Path -LiteralPath $marker)) { throw 'upgrade changed user data' }
    $checks += 'reinstall_preserves_user_data'
    $uninstallers = @(Get-ChildItem -LiteralPath $installRoot -Filter '*ninstall*.exe')
    if ($uninstallers.Count -ne 1) { throw 'uninstaller discovery ambiguous' }
    $process = Start-Process -FilePath $uninstallers[0].FullName -ArgumentList '/S' -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw ('uninstaller exit ' + $process.ExitCode) }
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ((Test-Path -LiteralPath (Join-Path $installRoot 'AcouAgent.exe')) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 300 }
    if (Test-Path -LiteralPath (Join-Path $installRoot 'AcouAgent.exe')) { throw 'application still installed' }
    if ((Get-FileHash -LiteralPath $vaultPath -Algorithm SHA256).Hash -ne $vaultHash -or -not (Test-Path -LiteralPath $marker)) { throw 'uninstall deleted user data' }
    $checks += 'uninstall_preserves_user_data'
    @{kind='actual_windows_installer_acceptance'; checks=$checks; full_solver_test=$false} | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 -LiteralPath (Join-Path $outputRoot 'report.json')
    Write-Output ('installer checks passed: ' + $checks.Count)
} finally {
    Remove-Item Env:ACOUAGENT_ELECTRON_EXE -ErrorAction SilentlyContinue
    Remove-Item Env:ACOUAGENT_DESKTOP_SMOKE_OUTPUT -ErrorAction SilentlyContinue
    Remove-Item Env:ACOUAGENT_DESKTOP_SMOKE_DEFAULT_PROFILE -ErrorAction SilentlyContinue
    $expectedProfile = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'AcouAgent\Desktop'))
    if ([IO.Path]::GetFullPath($profileRoot) -eq $expectedProfile -and (Test-Path -LiteralPath $profileRoot) -and -not (Test-Path -LiteralPath (Join-Path $installRoot 'AcouAgent.exe'))) {
        $retained = Join-Path $outputRoot 'preserved-default-profile'
        if (Test-Path -LiteralPath $retained) { throw 'retained profile destination exists; leave profile intact' }
        Move-Item -LiteralPath $profileRoot -Destination $retained
    }
    # Keep failure evidence and profile under OutputDirectory; delete only the exact owned temp workspace.
    $resolvedWork = [IO.Path]::GetFullPath($workRoot)
    if ($resolvedWork.StartsWith($tempRoot + '\', [StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolvedWork).StartsWith('acouagent-desktop-installer-') -and -not (Test-Path -LiteralPath (Join-Path $installRoot 'AcouAgent.exe'))) {
        Remove-Item -LiteralPath $resolvedWork -Recurse -Force
    }
}
