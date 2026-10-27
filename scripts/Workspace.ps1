# Shared portable workspace guards. No registry or credential-store mutations.
$FinkaWorkspaceMagic = 'FinkaHelper-owned-workspace-v1'
$script:FinkaLockOwned = $false

function Get-FinkaWorkRoot([string]$Bundle) {
    return [IO.Path]::GetFullPath((Join-Path $Bundle '.finka-work'))
}

function Assert-FinkaWorkspace([string]$Bundle) {
    $FinkaWork = Get-FinkaWorkRoot $Bundle
    $FinkaItem = Get-Item -LiteralPath $FinkaWork -Force -ErrorAction Stop
    if (-not $FinkaItem.PSIsContainer -or ($FinkaItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Workspace is not an ordinary directory. Cleanup stopped.'
    }
    $FinkaMark = Join-Path $FinkaWork '.finka-workspace'
    $FinkaMarkItem = Get-Item -LiteralPath $FinkaMark -Force -ErrorAction Stop
    if ($FinkaMarkItem.PSIsContainer -or ($FinkaMarkItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Workspace ownership marker must be an ordinary file.'
    }
    if ((Get-Content -LiteralPath $FinkaMark -Raw).Trim() -ne $FinkaWorkspaceMagic) {
        throw 'Workspace ownership marker does not match. Unknown data will not be removed.'
    }
    foreach ($FinkaTop in Get-ChildItem -LiteralPath $FinkaWork -Force) {
        if ($FinkaTop.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Redirected top-level workspace content is not supported.' }
    }
    return $FinkaWork
}

function Initialize-FinkaWorkspace([string]$Bundle) {
    $FinkaWork = Get-FinkaWorkRoot $Bundle
    $FinkaItem = Get-Item -LiteralPath $FinkaWork -Force -ErrorAction SilentlyContinue
    if ($FinkaItem -and ($FinkaItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Workspace is a reparse point. Startup stopped.'
    }
    if (-not $FinkaItem) { New-Item -ItemType Directory -Path $FinkaWork -ErrorAction Stop | Out-Null }
    $FinkaMark = Join-Path $FinkaWork '.finka-workspace'
    if (-not (Test-Path -LiteralPath $FinkaMark)) {
        if (Get-ChildItem -LiteralPath $FinkaWork -Force | Select-Object -First 1) {
            throw 'Existing workspace contains unknown files and will not be adopted.'
        }
        Set-Content -LiteralPath $FinkaMark -Value $FinkaWorkspaceMagic -Encoding ASCII
    }
    return Assert-FinkaWorkspace $Bundle
}

function Enter-FinkaWorkspaceLock([string]$Bundle) {
    $FinkaWork = Assert-FinkaWorkspace $Bundle
    $FinkaLock = Join-Path $FinkaWork '.running-lock'
    try { New-Item -ItemType Directory -Path $FinkaLock -ErrorAction Stop | Out-Null }
    catch {
        $FinkaItem = Get-Item -LiteralPath $FinkaLock -Force -ErrorAction Stop
        if (-not $FinkaItem.PSIsContainer -or ($FinkaItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Invalid run lock.' }
        $FinkaOldPid = (Get-Content -LiteralPath (Join-Path $FinkaLock 'pid') -Raw -ErrorAction Stop).Trim()
        if ($FinkaOldPid -notmatch '^[1-9][0-9]*$' -or (Get-Process -Id ([int]$FinkaOldPid) -ErrorAction SilentlyContinue)) {
            throw 'Helper or cleanup is still running. Close the original window and retry.'
        }
        Assert-FinkaLockIdle $FinkaLock
        Get-ChildItem -LiteralPath $FinkaLock -Force | Remove-Item -Force -ErrorAction Stop
        [IO.Directory]::Delete($FinkaLock)
        New-Item -ItemType Directory -Path $FinkaLock -ErrorAction Stop | Out-Null
    }
    Set-Content -LiteralPath (Join-Path $FinkaLock 'pid') -Value ([string]$PID) -Encoding ASCII
    $script:FinkaLockOwned = $true
}

function Assert-FinkaLockIdle([string]$FinkaLock, [switch]$IgnoreSelf) {
    foreach ($FinkaPidItem in Get-ChildItem -LiteralPath $FinkaLock -Force) {
        if (@('pid','bootstrap-pid','main-pid','worker-pid','auth-pid') -notcontains $FinkaPidItem.Name -or
            $FinkaPidItem.PSIsContainer -or ($FinkaPidItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unknown run-lock content.' }
        $FinkaRecordedPid = (Get-Content -LiteralPath $FinkaPidItem.FullName -Raw).Trim()
        if ($FinkaRecordedPid -notmatch '^[1-9][0-9]*$') { throw 'Invalid recorded process ID.' }
        if ($IgnoreSelf -and $FinkaRecordedPid -eq [string]$PID) { continue }
        if (Get-Process -Id ([int]$FinkaRecordedPid) -ErrorAction SilentlyContinue) { throw 'A helper child process is still running. Retry after it exits.' }
    }
}

function Exit-FinkaWorkspaceLock([string]$Bundle) {
    if (-not $script:FinkaLockOwned) { return }
    $FinkaLock = Join-Path (Get-FinkaWorkRoot $Bundle) '.running-lock'
    if (Test-Path -LiteralPath $FinkaLock) {
        $FinkaLockItem = Get-Item -LiteralPath $FinkaLock -Force
        if ($FinkaLockItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { return }
        if ((Get-Content -LiteralPath (Join-Path $FinkaLock 'pid') -Raw).Trim() -eq [string]$PID) {
            Assert-FinkaLockIdle $FinkaLock -IgnoreSelf
            Get-ChildItem -LiteralPath $FinkaLock -Force | Remove-Item -Force
            [IO.Directory]::Delete($FinkaLock)
        }
    }
    $script:FinkaLockOwned = $false
}

function Assert-FinkaCleanupSafe([string]$Bundle) {
    $FinkaWork = Assert-FinkaWorkspace $Bundle
    $FinkaAllowed = @('auth','cache','downloads','logs','pairing','tmp','tools','venvs',
        'python-3.13-macos-arm64','python-3.13-macos-x64','python-3.13-windows-x64',
        '.finka-workspace','.running-lock','.cleanup-request','.DS_Store','desktop.ini','Thumbs.db')
    foreach ($FinkaEntry in Get-ChildItem -LiteralPath $FinkaWork -Force) {
        if ($FinkaAllowed -notcontains $FinkaEntry.Name) { throw ('Unknown workspace content: ' + $FinkaEntry.Name) }
    }
    # Do not recurse through junctions, symlinks, or other reparse points in PowerShell 5.
    $FinkaStack = [System.Collections.Generic.Stack[string]]::new()
    $FinkaStack.Push($FinkaWork)
    while ($FinkaStack.Count) {
        $FinkaDirectory = $FinkaStack.Pop()
        foreach ($FinkaEntryPath in [IO.Directory]::EnumerateFileSystemEntries($FinkaDirectory)) {
            $FinkaAttrs = [IO.File]::GetAttributes($FinkaEntryPath)
            if (($FinkaAttrs -band [IO.FileAttributes]::ReparsePoint) -or ([IO.Path]::GetFileName($FinkaEntryPath) -eq '.git')) {
                throw ('Protected link or Git data found. Cleanup stopped: ' + $FinkaEntryPath)
            }
            if ($FinkaAttrs -band [IO.FileAttributes]::Directory) { $FinkaStack.Push($FinkaEntryPath) }
        }
    }
    return $FinkaWork
}
