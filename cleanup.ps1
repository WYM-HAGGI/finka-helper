param([switch]$Yes)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'scripts/Workspace.ps1')
try {
    $FinkaWork = Get-FinkaWorkRoot $PSScriptRoot
    if (-not (Get-Item -LiteralPath $FinkaWork -Force -ErrorAction SilentlyContinue)) {
        Write-Host 'No current workspace exists. You may delete the extracted folder and original ZIP manually.'
        exit 0
    }
    $FinkaWork = Assert-FinkaCleanupSafe $PSScriptRoot
    Write-Host ('Permanently remove: ' + $FinkaWork)
    Write-Host 'Includes private Python, tools, IPA, auth cache, local pairing copies, temporary files and logs.'
    Write-Host 'Save any IPA or logs you need first. The iPhone app, Apple Devices and system registry are not removed.'
    if (-not $Yes) {
        $FinkaChoice = Read-Host 'Type CLEAN to confirm, or Enter to keep everything'
        if ($FinkaChoice -cne 'CLEAN') { Write-Host 'Workspace kept.'; exit 0 }
    }
    Enter-FinkaWorkspaceLock $PSScriptRoot
    $FinkaWork = Assert-FinkaCleanupSafe $PSScriptRoot
    Set-Location -LiteralPath $PSScriptRoot
    Remove-Item -LiteralPath $FinkaWork -Recurse -Force -ErrorAction Stop
    if (Get-Item -LiteralPath $FinkaWork -Force -ErrorAction SilentlyContinue) { throw 'Some workspace content remains.' }
    $script:FinkaLockOwned = $false
    Write-Host 'Workspace cleanup completed. You can now delete the extracted folder and original ZIP.'
    Write-Host 'For older 1.0.0 data and optional Apple Devices removal, see the cleanup guide in this folder.'
    exit 0
} catch {
    Write-Host ('Cleanup NOT completed: ' + $_.Exception.Message)
    Write-Host 'Close programs using these files and retry. Unknown folders and protected data are not swept.'
    exit 2
} finally {
    Exit-FinkaWorkspaceLock $PSScriptRoot
}
