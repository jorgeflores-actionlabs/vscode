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