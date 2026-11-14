[CmdletBinding()]
param(
    [string]$RepositoryUrl = 'https://github.com/syedfahadiqbal7/VOICEASSISTANT.git',
    [string]$Branch = 'main'
)

$ErrorActionPreference = 'Stop'
$source = (Get-Location).Path
if (-not (Test-Path (Join-Path $source 'main.py')) -or -not (Test-Path (Join-Path $source 'requirements.txt'))) {
    throw 'Run this script from the extracted Voice Assistant project root (the folder containing main.py and requirements.txt).'
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is required. Install Git for Windows: https://git-scm.com/download/win'
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('voiceassistant-github-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
$repoPath = Join-Path $tempRoot 'repository'

try {
    Write-Host "Checking GitHub repository: $RepositoryUrl"
    $remoteRefs = & git ls-remote --exit-code --heads $RepositoryUrl $Branch 2>&1
    $lsRemoteCode = $LASTEXITCODE

    if ($lsRemoteCode -eq 0) {
        & git clone --branch $Branch $RepositoryUrl $repoPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not clone the target branch. Check GitHub authentication and repository access.' }
    } elseif ($lsRemoteCode -eq 2) {
        & git init --initial-branch=$Branch $repoPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the local Git repository.' }
        & git -C $repoPath remote add origin $RepositoryUrl
        if ($LASTEXITCODE -ne 0) { throw 'Could not configure the GitHub remote.' }
    } else {
        throw "Unable to read the GitHub repository. Git returned: $remoteRefs"
    }

    # Copy source, excluding private/runtime data and machine-specific generated files.
    $excludeDirs = @('.git', '.venv', 'venv', 'env', 'BackupFiles', 'history', 'exports', '.idea', '.dart_tool', 'build', '.pytest_cache', '__pycache__')
    $excludeFiles = @('*.pyc', '*.log', '*.new', '*.iml', '.env', 'local.properties', 'Thumbs.db', '.DS_Store')
    $robocopyArgs = @($source, $repoPath, '/E', '/R:2', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    $robocopyArgs += '/XD'
    $robocopyArgs += $excludeDirs
    $robocopyArgs += '/XF'
    $robocopyArgs += $excludeFiles
    & robocopy @robocopyArgs
    $copyCode = $LASTEXITCODE
    if ($copyCode -ge 8) { throw "Source copy failed (robocopy exit code $copyCode)." }

    # Restore only safe, non-user-specific history templates and placeholders.
    $safeHistory = Join-Path $repoPath 'history'
    New-Item -ItemType Directory -Force -Path $safeHistory | Out-Null
    foreach ($name in @('.gitkeep', 'README.md', 'llm_failover.example.json')) {
        $candidate = Join-Path $source (Join-Path 'history' $name)
        if (Test-Path $candidate) { Copy-Item -Force $candidate (Join-Path $safeHistory $name) }
    }
    $safeExports = Join-Path $repoPath 'exports'
    New-Item -ItemType Directory -Force -Path $safeExports | Out-Null
    $exportsPlaceholder = Join-Path $source 'exports/.gitkeep'
    if (Test-Path $exportsPlaceholder) { Copy-Item -Force $exportsPlaceholder (Join-Path $safeExports '.gitkeep') }

    & git -C $repoPath add --all
    if ($LASTEXITCODE -ne 0) { throw 'git add failed.' }
    & git -C $repoPath diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'GitHub is already synchronized with this source package.' -ForegroundColor Green
        return
    }

    $gitName = (& git config --get user.name 2>$null)
    $gitEmail = (& git config --get user.email 2>$null)
    if (-not $gitName -or -not $gitEmail) {
        throw "Configure your Git identity first: git config --global user.name 'Your Name' ; git config --global user.email 'you@example.com'"
    }

    & git -C $repoPath commit -m 'Publish complete Voice Assistant project'
    if ($LASTEXITCODE -ne 0) { throw 'Git commit failed.' }
    & git -C $repoPath push --set-upstream origin $Branch
    if ($LASTEXITCODE -ne 0) {
        throw 'Push failed. Check GitHub authentication/branch protection, then rerun this script. It never force-pushes.'
    }

    Write-Host ''
    Write-Host 'Voice Assistant source successfully pushed to GitHub.' -ForegroundColor Green
    Write-Host "Repository: https://github.com/syedfahadiqbal7/VOICEASSISTANT"
    Write-Host 'GitHub Actions will start automatically after the push.'
} finally {
    if (Test-Path $tempRoot) { Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue }
}
