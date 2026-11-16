[CmdletBinding()]
param([string]$Machine = 'podman-machine-default')

$ErrorActionPreference = 'Stop'
$taskPluginRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$taskImage = 'localhost/primigenius/sra-fastq-auto:2.0.0'
$taskBundledPodman = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $taskPluginRoot))) 'resources\podman\podman.exe'

if (Test-Path -LiteralPath $taskBundledPodman) {
    & $taskBundledPodman build --jobs=1 -t $taskImage -f (Join-Path $taskPluginRoot 'Dockerfile') $taskPluginRoot
} elseif (Get-Command podman -ErrorAction SilentlyContinue) {
    & podman build --jobs=1 -t $taskImage -f (Join-Path $taskPluginRoot 'Dockerfile') $taskPluginRoot
} else {
    $taskLinuxRoot = (& wsl.exe -d $Machine -u root --exec wslpath -a ($taskPluginRoot -replace '\\', '/')).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $taskLinuxRoot) {
        throw 'Cannot locate the plugin in the Podman WSL machine.'
    }
    & wsl.exe -d $Machine -u root --exec podman build --jobs=1 -t $taskImage -f "$taskLinuxRoot/Dockerfile" $taskLinuxRoot
}
if ($LASTEXITCODE -ne 0) { throw "Image build failed with exit code $LASTEXITCODE" }

if (Get-Command podman -ErrorAction SilentlyContinue) {
    & podman run --rm $taskImage sh -lc 'fasterq-dump --version && vdb-validate --version && pigz --version && python3 --version'
} else {
    & wsl.exe -d $Machine -u root --exec podman run --rm $taskImage sh -lc 'fasterq-dump --version && vdb-validate --version && pigz --version && python3 --version'
}
if ($LASTEXITCODE -ne 0) { throw "Runtime verification failed with exit code $LASTEXITCODE" }
Write-Output "Ready: $taskImage"

