[CmdletBinding()]
param(
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

function New-RandomToken {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

function Get-EnvValue([string]$Name) {
    $line = Get-Content -LiteralPath '.env' | Where-Object { $_ -like "$Name=*" } | Select-Object -First 1
    if ($null -eq $line) { return '' }
    return $line.Substring($Name.Length + 1)
}

if (-not (Test-Path -LiteralPath '.env')) {
    if (-not (Test-Path -LiteralPath '.env.example')) { throw 'missing .env.example' }
    $mcpToken = New-RandomToken
    do { $executorToken = New-RandomToken } while ($executorToken -eq $mcpToken)
    $content = Get-Content -LiteralPath '.env.example' -Raw
    $content = $content.Replace('replace-with-at-least-32-random-characters', $mcpToken)
    $content = $content.Replace('replace-with-a-different-32-character-secret', $executorToken)
    [System.IO.File]::WriteAllText(
        (Join-Path $repoRoot '.env'),
        $content,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Output 'created .env with independent local tokens'
} else {
    Write-Output 'using existing .env without overwriting it'
}

$envLines = Get-Content -LiteralPath '.env'
$mcpCount = @($envLines | Where-Object { $_ -like 'MCP_AUTH_TOKEN=*' }).Count
$executorCount = @($envLines | Where-Object { $_ -like 'EXECUTOR_SHARED_TOKEN=*' }).Count
if ($mcpCount -ne 1 -or $executorCount -ne 1) {
    throw 'MCP_AUTH_TOKEN and EXECUTOR_SHARED_TOKEN must each appear exactly once in .env'
}
$mcpToken = Get-EnvValue 'MCP_AUTH_TOKEN'
$executorToken = Get-EnvValue 'EXECUTOR_SHARED_TOKEN'
if ($mcpToken.Length -lt 32 -or $executorToken.Length -lt 32) {
    throw 'MCP_AUTH_TOKEN and EXECUTOR_SHARED_TOKEN must each contain at least 32 characters'
}
if ($mcpToken.StartsWith('replace-') -or $executorToken.StartsWith('replace-')) {
    throw 'replace the token placeholders in .env before starting'
}
if ($mcpToken -eq $executorToken) {
    throw 'MCP_AUTH_TOKEN and EXECUTOR_SHARED_TOKEN must be different'
}

if ($PrepareOnly) {
    Write-Output 'configuration prepared; add a server model credential to .env when natural-language chat is required'
    exit 0
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'docker is required' }
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) { throw 'docker compose is required' }

& docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'docker compose config failed' }
& docker compose pull jwave-executor
if ($LASTEXITCODE -ne 0) { throw 'executor image pull failed' }
& docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'stack startup failed' }

$gatewayReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 3
        if ([string]$health -eq 'ok') { $gatewayReady = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $gatewayReady) {
    & docker compose logs dify-mcp
    throw 'gateway did not become healthy'
}

$bootstrapDone = $false
for ($attempt = 0; $attempt -lt 180; $attempt++) {
    $state = (& docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' acouagent-skill-bootstrap 2>$null)
    if ($state -eq 'exited 0') { $bootstrapDone = $true; break }
    if ($state -like 'exited *' -and $state -ne 'exited 0') {
        & docker compose logs skill-bootstrap
        throw 'fixed-skill bootstrap failed'
    }
    Start-Sleep -Seconds 5
}
if (-not $bootstrapDone) {
    & docker compose logs skill-bootstrap
    throw 'fixed-skill bootstrap timed out'
}

& docker exec dify-mcp python -m scripts.skill_library.local_basic status-all *> $null
if ($LASTEXITCODE -ne 0) { throw 'fixed-skill inventory is incomplete' }
Write-Output 'AcouAgent is ready at http://127.0.0.1:8001/portal'
if ([string]::IsNullOrEmpty((Get-EnvValue 'MODEL_API_KEY')) -and [string]::IsNullOrEmpty((Get-EnvValue 'MODEL_PROFILES_JSON'))) {
    Write-Output 'open Model Settings in the portal to enter your BYOK endpoint, model and API key'
}
