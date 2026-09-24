$env:Path += ";C:\Users\jorge.flores\Documents\python"

git add .
git commit -m "Changes"
#git remote add origin https://github.com/USUARIO/REPOSITORIO.git
git push -u origin master


$pythonDir = Split-Path (Get-Command python).Source
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
$paths = @(
    $pythonDir
    (Join-Path $pythonDir "Scripts")
)

foreach ($path in $paths) {
    if ($currentPath -notlike "*$path*") {
        $currentPath = "$currentPath;$path"
    }
}

[Environment]::SetEnvironmentVariable("Path", $currentPath, "User")



$profilePath = $PROFILE.CurrentUserCurrentHost
$profileDirectory = Split-Path -Parent $profilePath

New-Item -ItemType Directory -Path $profileDirectory -Force | Out-Null

if (-not (Test-Path -LiteralPath $profilePath)) {
    New-Item -ItemType File -Path $profilePath -Force | Out-Null
}


# Stage, commit, and push all changes in one Git repository.
function Invoke-GitSave {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not (Test-Path -LiteralPath $RepositoryPath)) {
        Write-Warning "Repository path not found: $RepositoryPath"
        return
    }

    Push-Location -LiteralPath $RepositoryPath
    try {
        $null = git rev-parse --is-inside-work-tree 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Skipping non-Git folder: $RepositoryPath"
            return
        }

        Write-Host ""
        Write-Host "Saving repository: $RepositoryPath"

        git add -A
        if ($LASTEXITCODE -ne 0) {
            throw "git add failed in $RepositoryPath."
        }

        git diff --cached --quiet
        $diffExitCode = $LASTEXITCODE
        if ($diffExitCode -eq 0) {
            Write-Host "There are no new changes to commit. Checking for commits to push."
        }
        elseif ($diffExitCode -eq 1) {
            git commit -m $Message
            if ($LASTEXITCODE -ne 0) {
                throw "git commit failed in $RepositoryPath."
            }
        }
        else {
            throw "Unable to inspect the staged changes in $RepositoryPath."
        }

        $currentBranch = git branch --show-current
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to determine the current Git branch in $RepositoryPath."
        }
        if ([string]::IsNullOrWhiteSpace($currentBranch)) {
            Write-Warning "Changes were committed locally, but push was skipped because HEAD is detached."
            return
        }

        $upstream = git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($upstream)) {
            git push
        }
        else {
            $remotes = @(git remote)
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to inspect Git remotes in $RepositoryPath."
            }

            if ($remotes.Count -eq 0) {
                Write-Warning (
                    "Changes were committed locally, but no Git remote is configured for $RepositoryPath. " +
                    "Add one with: git remote add origin <repository-url>"
                )
                return
            }

            if ($remotes -contains "origin") {
                $remoteName = "origin"
            }
            elseif ($remotes.Count -eq 1) {
                $remoteName = $remotes[0]
            }
            else {
                Write-Warning (
                    "Changes were committed locally, but push was skipped because multiple " +
                    "remotes exist and none is named 'origin': $($remotes -join ', ')"
                )
                return
            }

            Write-Host "No upstream configured. Using remote '$remoteName' for branch '$currentBranch'."
            git push --set-upstream $remoteName $currentBranch
        }

        if ($LASTEXITCODE -ne 0) {
            Write-Error (
                "git push failed in $RepositoryPath. Verify the remote URL with 'git remote -v' and confirm " +
                "that you have access to the repository. The commit remains saved locally."
            )
            return
        }

        Write-Host "Repository saved successfully: $RepositoryPath"
    }
    finally {
        Pop-Location
    }
}

# Stage, commit, and push all changes in sqlserver\app and sqlserver\api.
# Usage: SAVE
#        SAVE "Descriptive commit message"
function SAVE {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0)]
        [string]$Message = "LV - SQL Server Statements"
    )

    $scriptDirectory = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $sqlserverDirectory = Split-Path -Parent $scriptDirectory
    $repositories = @(
        (Join-Path $sqlserverDirectory "app")
        (Join-Path $sqlserverDirectory "api")
    )

    foreach ($repository in $repositories) {
        Invoke-GitSave -RepositoryPath $repository -Message $Message
    }

    Write-Host ""
    Write-Host "SAVE completed for sqlserver\app and sqlserver\api."
}

