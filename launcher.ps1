param([switch]$Doctor, [switch]$DownloadOnly)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONNOUSERSITE = '1'
. (Join-Path $PSScriptRoot 'scripts/Workspace.ps1')
$FinkaBootstrapLog = $null
function Write-FinkaStage([string]$Message) {
    Write-Host $Message
    if ($FinkaBootstrapLog) {
        Add-Content -LiteralPath $FinkaBootstrapLog -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Message) -Encoding UTF8
    }
}

try {
    $FinkaManifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $FinkaBuild = [int](Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name CurrentBuildNumber)
    if ($FinkaBuild -lt 19045) { throw 'Windows 10 22H2 or Windows 11 is required.' }
    $FinkaMachine = $env:PROCESSOR_ARCHITEW6432
    if (-not $FinkaMachine) { $FinkaMachine = $env:PROCESSOR_ARCHITECTURE }
    if ($FinkaMachine -match 'ARM64') {
        if ($FinkaBuild -lt 22000) { throw 'Windows on ARM requires Windows 11 for x64 emulation.' }
        $FinkaPlatform = 'windows-x64'
        Write-Host '[Environment] Windows 11 ARM detected. Using x64 emulation for dependency compatibility.'
    }
    elseif ($FinkaMachine -match 'AMD64') { $FinkaPlatform = 'windows-x64' }
    else { throw 'Only Windows x64 and ARM64 are supported.' }
    $FinkaDataDir = Initialize-FinkaWorkspace $PSScriptRoot
    Enter-FinkaWorkspaceLock $PSScriptRoot
    $FinkaOldRequest = Join-Path $FinkaDataDir '.cleanup-request'
    $FinkaOldRequestItem = Get-Item -LiteralPath $FinkaOldRequest -Force -ErrorAction SilentlyContinue
    if ($FinkaOldRequestItem) {
        if (($FinkaOldRequestItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $FinkaOldRequestItem.PSIsContainer -or
            ((Get-Content -LiteralPath $FinkaOldRequest -Raw).Trim() -ne $FinkaWorkspaceMagic)) { throw 'Unknown cleanup request. Startup stopped.' }
        Remove-Item -LiteralPath $FinkaOldRequest -ErrorAction Stop
    }
    $FinkaLogDir = Join-Path $FinkaDataDir 'logs'
    New-Item -ItemType Directory -Force -Path $FinkaLogDir | Out-Null
    $FinkaBootstrapLog = Join-Path $FinkaLogDir ('launcher-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    $FinkaRuntimeDir = Join-Path $FinkaDataDir ('python-3.13-' + $FinkaPlatform)
    $FinkaAsset = $FinkaManifest.platforms.$FinkaPlatform.python
    $FinkaPython = Join-Path $FinkaRuntimeDir 'python\python.exe'
    $FinkaMarker = Join-Path $FinkaRuntimeDir 'runtime.ready'
    New-Item -ItemType Directory -Force -Path $FinkaRuntimeDir | Out-Null
    Write-FinkaStage '[Environment] Checking Windows architecture and isolated runtime...'
    $FinkaReady = (Test-Path -LiteralPath $FinkaPython) -and (Test-Path -LiteralPath $FinkaMarker)
    if ($FinkaReady) { $FinkaReady = ((Get-Content -LiteralPath $FinkaMarker -Raw).Trim() -eq $FinkaAsset.sha256) }
    if (-not $FinkaReady) {
        $FinkaArchive = Join-Path $FinkaRuntimeDir 'official-python.tar.gz'
        $FinkaCached = Test-Path -LiteralPath $FinkaArchive
        if ($FinkaCached) { $FinkaCached = ((Get-FileHash -LiteralPath $FinkaArchive -Algorithm SHA256).Hash.ToLowerInvariant() -eq $FinkaAsset.sha256) }
        if (-not $FinkaCached) {
            Write-FinkaStage '[Download] Preparing official portable Python. No system Python is required.'
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $FinkaAsset.url -OutFile ($FinkaArchive + '.partial') -UseBasicParsing -TimeoutSec 1200
            $FinkaActual = (Get-FileHash -LiteralPath ($FinkaArchive + '.partial') -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($FinkaActual -ne $FinkaAsset.sha256) { throw 'Python SHA-256 mismatch. Downloaded content will not be executed.' }
            Move-Item -LiteralPath ($FinkaArchive + '.partial') -Destination $FinkaArchive -Force
        }
        Write-FinkaStage '[Verify] Official runtime SHA-256 passed. Extracting...'
        & tar.exe -xzf $FinkaArchive -C $FinkaRuntimeDir
        if ($LASTEXITCODE -ne 0) { throw 'Cannot extract Python. Windows 10 22H2/Windows 11 with tar.exe is required.' }
        if (-not (Test-Path -LiteralPath $FinkaPython)) { throw 'Python executable is missing after extraction.' }
        Set-Content -LiteralPath $FinkaMarker -Value $FinkaAsset.sha256 -Encoding ASCII
    }
    $FinkaArguments = @('-u', (Join-Path $PSScriptRoot 'bootstrap.py'))
    if ($Doctor) { $FinkaArguments += '--doctor' }
    if ($DownloadOnly) { $FinkaArguments += '--download-only' }
    Push-Location -LiteralPath $PSScriptRoot
    try {
        & $FinkaPython @FinkaArguments
        $FinkaExitCode = $LASTEXITCODE
    } finally { Pop-Location }
    Write-FinkaStage ('[Result] Helper exit code: ' + $FinkaExitCode)
    Write-Host ('Launcher log: ' + $FinkaBootstrapLog)
    $FinkaCleanupRequest = Join-Path $FinkaDataDir '.cleanup-request'
    if ((Test-Path -LiteralPath $FinkaCleanupRequest) -and ((Get-Content -LiteralPath $FinkaCleanupRequest -Raw).Trim() -eq $FinkaWorkspaceMagic)) {
        Exit-FinkaWorkspaceLock $PSScriptRoot
        $FinkaBootstrapLog = $null
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'cleanup.ps1') -Yes
        if ($LASTEXITCODE -ne 0 -and $FinkaExitCode -eq 0) { $FinkaExitCode = 2 }
    }
    exit $FinkaExitCode
} catch {
    Write-FinkaStage ('[Failed] ' + $_.Exception.Message)
    if ($FinkaBootstrapLog) { Write-Host ('Launcher log: ' + $FinkaBootstrapLog) }
    Write-Host 'Extract the whole ZIP first. Check network access to github.com and pypi.org, then retry.'
    exit 1
} finally {
    Exit-FinkaWorkspaceLock $PSScriptRoot
}
