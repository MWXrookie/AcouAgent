[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Baseline,
    [Parameter(Mandatory=$true)][string]$Candidate,
    [string]$BaselineVersion='0.2.0-alpha.1',
    [string]$CandidateVersion='0.2.0-alpha.2',
    [string]$ExistingOwnershipRecord,
    [string]$OutputDirectory='outputs/desktop-upgrade-acceptance'
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$outputRoot=[IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
$programRoot=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\AcouAgent'))
$profileRoot=Join-Path $env:LOCALAPPDATA 'AcouAgent\Desktop'
$oldInstaller=(Resolve-Path -LiteralPath $Baseline).Path
$newInstaller=(Resolve-Path -LiteralPath $Candidate).Path
$dockerPath=Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
if(-not(Test-Path -LiteralPath $dockerPath)){$dockerPath=Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'}
if(Get-Process AcouAgent -ErrorAction SilentlyContinue){throw 'close AcouAgent before the controlled installer upgrade test'}
$vaultPath=Join-Path $profileRoot 'credentials.bin'
if(-not(Test-Path -LiteralPath $vaultPath)){throw 'existing validated desktop profile required'}
$vaultHash=(Get-FileHash -LiteralPath $vaultPath -Algorithm SHA256).Hash
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$ownershipPath=Join-Path $outputRoot 'installation-ownership.json'
if($ExistingOwnershipRecord -and -not(Test-Path -LiteralPath $ownershipPath)){
    $known=Get-Content -LiteralPath (Resolve-Path -LiteralPath $ExistingOwnershipRecord).Path -Raw | ConvertFrom-Json
    if($known.programRoot -ne $programRoot -or $known.vaultHash -ne $vaultHash -or $known.purpose -ne 'controlled_acouagent_upgrade_acceptance'){throw 'previous owned installation does not match this profile'}
    $known | ConvertTo-Json | Set-Content -LiteralPath $ownershipPath -Encoding utf8
}
if(Test-Path -LiteralPath $ownershipPath){
    $owned=Get-Content -LiteralPath $ownershipPath -Raw | ConvertFrom-Json
    if($owned.programRoot -ne $programRoot -or $owned.vaultHash -ne $vaultHash){throw 'owned installation identity mismatch'}
}else{
    if(Test-Path -LiteralPath $programRoot){throw 'existing program directory is not owned by this test'}
    $registrations=Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall -ErrorAction SilentlyContinue | Get-ItemProperty | Where-Object DisplayName -eq 'AcouAgent'
    if($registrations){throw 'existing AcouAgent installation found; refuse to replace it'}
    @{programRoot=$programRoot;vaultHash=$vaultHash;purpose='controlled_acouagent_upgrade_acceptance'} | ConvertTo-Json | Set-Content -LiteralPath $ownershipPath -Encoding utf8
}
$images=Get-Content (Join-Path $repoRoot 'desktop/runtime/images.json') -Raw | ConvertFrom-Json
if($images.gateway -notmatch '^ghcr\.io/mwxrookie/acouagent-gateway@sha256:[a-f0-9]{64}$'){throw 'unreviewed probe image'}
$probeCode=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'data_fingerprint.py'))
$checks=@()
function Run-Window([string]$Executable,[string]$Version,[string]$Name){
    $env:ACOUAGENT_ELECTRON_EXE=$Executable
    $env:ACOUAGENT_DESKTOP_UAT_OUTPUT=Join-Path $outputRoot $Name
    $env:ACOUAGENT_DESKTOP_UAT_EXPECT_VERSION=$Version
    $env:ACOUAGENT_DESKTOP_UAT_STOP='true'
    Remove-Item Env:ACOUAGENT_DESKTOP_UAT_BACKUP,Env:ACOUAGENT_DESKTOP_UAT_RESTORE,Env:ACOUAGENT_DESKTOP_UAT_INTERRUPT,Env:ACOUAGENT_DESKTOP_UAT_SKILLS -ErrorAction SilentlyContinue
    & node (Join-Path $PSScriptRoot 'windows_runtime_uat.mjs')
    if($LASTEXITCODE -ne 0){throw ('actual installed window failed: '+$Name)}
    if((Get-FileHash -LiteralPath $vaultPath -Algorithm SHA256).Hash -ne $vaultHash){throw 'upgrade changed encrypted credentials'}
}
function Install-Version([string]$Installer){
    if(Get-Process AcouAgent -ErrorAction SilentlyContinue){throw 'application is still running; refuse installer mutation'}
    $installerProcess=Start-Process -FilePath $Installer -ArgumentList @('/S',('/D='+$programRoot)) -WindowStyle Hidden -PassThru -Wait
    if($installerProcess.ExitCode -ne 0){throw ('installer failed: '+$installerProcess.ExitCode)}
    if(-not(Test-Path -LiteralPath (Join-Path $programRoot 'AcouAgent.exe'))){throw 'installed application missing'}
    if((Get-FileHash -LiteralPath $vaultPath -Algorithm SHA256).Hash -ne $vaultHash){throw 'installer changed encrypted credentials'}
}
function Snapshot([string]$Name){
    $settings=Get-Content (Join-Path $profileRoot 'settings.json') -Raw | ConvertFrom-Json
    $volume=if($settings.dataVolume){$settings.dataVolume}else{'acouagent-desktop_desktop_data'}
    if($volume -notmatch '^(acouagent-desktop_desktop_data|acouagent-desktop-data-[a-f0-9]{32})$'){throw 'unexpected data volume'}
    $running=& $dockerPath --context desktop-linux ps --filter label=com.docker.compose.project=acouagent-desktop --format '{{.Names}}'
    if($LASTEXITCODE -ne 0 -or $running){throw 'all desktop services must be stopped before the logical snapshot'}
    $snapshot=& $dockerPath --context desktop-linux run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --memory 256m --cpus 0.5 --pids-limit 32 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m --mount ('type=volume,src='+$volume+',dst=/qa,readonly') --entrypoint python $images.gateway -c $probeCode
    if($LASTEXITCODE -ne 0){throw 'logical snapshot probe failed'}
    $result=Join-Path $outputRoot ($Name+'.json')
    [IO.File]::WriteAllText($result,($snapshot -join "`n"),[Text.UTF8Encoding]::new($false))
    return $result
}
function Compare-Snapshot([string]$Before,[string]$After,[bool]$Exact){
    $comparison=@'
import json,sys
before=json.load(open(sys.argv[1],encoding='utf-8-sig'));after=json.load(open(sys.argv[2],encoding='utf-8-sig'))
if sys.argv[3]=='True':
    assert before==after,'installer altered data'
else:
    assert all(after['files'].get(name)==value for name,value in before['files'].items()),'existing data file changed'
    for name,tables in before['databases'].items():
        for table,old in tables.items():
            new=after['databases'][name][table]
            assert old['columns']==new['columns'] and old['definitionSha256']==new['definitionSha256'],'existing table schema changed'
            assert all(new['rows'].get(row,0)>=count for row,count in old['rows'].items()),'existing row changed or disappeared'
print('data comparison passed')
'@
    & py -3.12 -X utf8 -c $comparison $Before $After ([string]$Exact)
    if($LASTEXITCODE -ne 0){throw 'data comparison failed'}
}
try{
    # Baseline packaged app stops only its verified, owned project before probes.
    Run-Window (Join-Path (Split-Path -Parent $oldInstaller) 'win-unpacked\AcouAgent.exe') $BaselineVersion 'baseline-stop'
    $initial=Snapshot '00-initial'
    Install-Version $oldInstaller
    $afterInitial=Snapshot '01-first-install'
    Compare-Snapshot $initial $afterInitial $true
    $checks+='baseline_install_preserves_all_existing_data'
    Run-Window (Join-Path $programRoot 'AcouAgent.exe') $BaselineVersion 'installed-baseline'
    $beforeUpgrade=Snapshot '02-before-upgrade'
    Install-Version $newInstaller
    $afterInstaller=Snapshot '03-after-upgrade-installer'
    Compare-Snapshot $beforeUpgrade $afterInstaller $true
    $checks+='upgrade_installer_preserves_all_existing_data'
    Run-Window (Join-Path $programRoot 'AcouAgent.exe') $CandidateVersion 'installed-candidate'
    $afterUpgrade=Snapshot '04-after-upgraded-runtime'
    Compare-Snapshot $beforeUpgrade $afterUpgrade $false
    $checks+='upgraded_runtime_preserves_existing_rows_and_assets'
    Install-Version $oldInstaller
    $afterRollbackInstaller=Snapshot '05-after-rollback-installer'
    Compare-Snapshot $afterUpgrade $afterRollbackInstaller $true
    Run-Window (Join-Path $programRoot 'AcouAgent.exe') $BaselineVersion 'installed-rollback'
    $afterRollback=Snapshot '06-after-rollback-runtime'
    Compare-Snapshot $afterUpgrade $afterRollback $false
    $checks+='rollback_preserves_existing_rows_and_assets'
    Install-Version $newInstaller
    $afterFinalInstall=Snapshot '07-after-final-install'
    Compare-Snapshot $afterRollback $afterFinalInstall $true
    Run-Window (Join-Path $programRoot 'AcouAgent.exe') $CandidateVersion 'installed-final'
    $afterFinal=Snapshot '08-after-final-runtime'
    Compare-Snapshot $afterRollback $afterFinal $false
    $checks+='reupgrade_preserves_existing_rows_and_assets'
    $checks+='encrypted_credentials_preserved_through_all_steps'
    @{kind='actual_windows_installer_upgrade_rollback';checks=$checks;baseline=$BaselineVersion;candidate=$CandidateVersion;baseline_sha256=(Get-FileHash $oldInstaller -Algorithm SHA256).Hash;candidate_sha256=(Get-FileHash $newInstaller -Algorithm SHA256).Hash;model_simulation_tested=$false;schema_migration_tested=$false;installed_program_preserved=$true} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $outputRoot 'report.json') -Encoding utf8
    Write-Output ('upgrade checks passed: '+$checks.Count)
}finally{
    Remove-Item Env:ACOUAGENT_ELECTRON_EXE,Env:ACOUAGENT_DESKTOP_UAT_OUTPUT,Env:ACOUAGENT_DESKTOP_UAT_EXPECT_VERSION,Env:ACOUAGENT_DESKTOP_UAT_STOP -ErrorAction SilentlyContinue
}
