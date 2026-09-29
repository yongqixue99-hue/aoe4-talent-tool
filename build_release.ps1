[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
function ConvertFrom-Base64Utf8 {
    param([Parameter(Mandatory = $true)][string]$Value)
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Value))
}

$projectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$sourcePath = Join-Path $projectRoot 'AoE4_RogueTalentTool.py'
$testsPath = Join-Path $projectRoot 'tests'
$assetsPath = Join-Path $projectRoot 'build_assets'
$releasePath = Join-Path $projectRoot 'release'
$workPath = Join-Path $projectRoot 'build_work'
$shareDirName = ConvertFrom-Base64Utf8 '5YiG5Lqr54mIX+WFjeWuieijhQ=='
$shareExeName = ConvertFrom-Base64Utf8 '5bid5Zu95pe25LujNOWkqei1i+eCueS/ruaUueWZqC5leGU='
$versionedZipName = ConvertFrom-Base64Utf8 '5bid5Zu95pe25LujNOWkqei1i+eCueS/ruaUueWZqF92MS40LjBf5YWN5a6J6KOFLnppcA=='
$compatZipName = ConvertFrom-Base64Utf8 '5bid5Zu95pe25LujNOWkqei1i+eCueS/ruaUueWZqF/liIbkuqvniYguemlw'
$guideName = ConvertFrom-Base64Utf8 '5L2/55So6K+05piOLnR4dA=='
$sharePath = Join-Path $projectRoot $shareDirName
$releaseExe = Join-Path $releasePath 'AoE4_RogueTalentTool.exe'
$shareExe = Join-Path $sharePath $shareExeName
$versionedZip = Join-Path $projectRoot $versionedZipName
$compatZip = Join-Path $projectRoot $compatZipName
$pythonExe = (Get-Command python -ErrorAction Stop).Source

& $pythonExe -m unittest discover -s $testsPath -v
if ($LASTEXITCODE -ne 0) {
    throw 'Tests failed; release was not built.'
}

New-Item -ItemType Directory -Path $releasePath -Force | Out-Null
New-Item -ItemType Directory -Path $workPath -Force | Out-Null
New-Item -ItemType Directory -Path $sharePath -Force | Out-Null

$pyInstallerArgs = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--windowed',
    '--noupx',
    '--log-level', 'WARN',
    '--name', 'AoE4_RogueTalentTool',
    '--distpath', $releasePath,
    '--workpath', (Join-Path $workPath 'cache'),
    '--specpath', $workPath,
    '--version-file', (Join-Path $assetsPath 'version_info.txt'),
    '--manifest', (Join-Path $assetsPath 'app.manifest'),
    $sourcePath
)
& $pythonExe @pyInstallerArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $releaseExe)) {
    throw 'PyInstaller did not produce the expected executable.'
}

$selfTestProcess = Start-Process -FilePath $releaseExe -ArgumentList '--self-test' `
    -WindowStyle Hidden -Wait -PassThru
if ($selfTestProcess.ExitCode -ne 0) {
    throw 'The packaged executable failed its built-in self-test.'
}
$guiSmokeProcess = Start-Process -FilePath $releaseExe -ArgumentList '--smoke-test' `
    -WindowStyle Hidden -Wait -PassThru
if ($guiSmokeProcess.ExitCode -ne 0) {
    throw 'The packaged executable failed its GUI startup smoke-test.'
}

Copy-Item -LiteralPath $releaseExe -Destination $shareExe -Force
Copy-Item -LiteralPath (Join-Path $projectRoot $guideName) `
    -Destination (Join-Path $sharePath 'README.txt') -Force

$releaseHash = (Get-FileHash -LiteralPath $releaseExe -Algorithm SHA256).Hash.ToLowerInvariant()
$shareHash = (Get-FileHash -LiteralPath $shareExe -Algorithm SHA256).Hash.ToLowerInvariant()
"$releaseHash *AoE4_RogueTalentTool.exe" |
    Set-Content -LiteralPath (Join-Path $releasePath 'SHA256SUMS.txt') -Encoding UTF8
"$shareHash *$shareExeName" |
    Set-Content -LiteralPath (Join-Path $sharePath 'SHA256SUMS.txt') -Encoding UTF8

Compress-Archive -LiteralPath $sharePath -DestinationPath $versionedZip -Force
Copy-Item -LiteralPath $versionedZip -Destination $compatZip -Force

Write-Host "Release ready: $versionedZip"
Write-Host "SHA256: $shareHash"
