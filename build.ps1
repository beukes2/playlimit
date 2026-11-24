# Build PlayLimit: real PyInstaller app -> native self-extracting launcher.
# The published dist\PlayLimit.exe is the launcher; it embeds build\app\PlayLimit.exe.
# Commit version.txt + version_info.txt + dist\PlayLimit.exe + dist\PlayLimit.exe.sha256.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$version = (Get-Content version.txt -Raw).Trim()
if (-not $version) { throw "version.txt is empty" }
$four = $version
if (($version -split '\.').Count -lt 4) { $four = $version + ".0" }

$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "csc.exe not found at $csc" }

Write-Host "Building PlayLimit $version" -ForegroundColor Cyan
Write-Host "PyInstaller: $(py -m PyInstaller --version)"

# 1) Real app (kept out of dist\ so it can be embedded in the launcher).
if (Test-Path "build\app") { Remove-Item "build\app" -Recurse -Force }
py -m PyInstaller --noconfirm --clean --onefile --noconsole --name PlayLimit `
    --icon playlimit.ico --version-file version_info.txt `
    --distpath build\app --workpath build\pyi --specpath . `
    --hidden-import pystray --hidden-import PIL --hidden-import psutil `
    --splash splash.png albion_limiter.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$real = "build\app\PlayLimit.exe"
$realVer = (Get-Item $real).VersionInfo.FileVersion
Write-Host "real app: v$realVer, $((Get-Item $real).Length) bytes"
if ("$realVer".Trim() -ne $version) { throw "real app version $realVer != version.txt $version" }

# 2) Generated assembly metadata for the native launcher/helper.
$vi = @"
using System.Reflection;
[assembly: AssemblyTitle("PlayLimit")]
[assembly: AssemblyDescription("PlayLimit - Albion Online Parental Time Limiter")]
[assembly: AssemblyCompany("PlayLimit")]
[assembly: AssemblyProduct("PlayLimit")]
[assembly: AssemblyFileVersion("$version")]
[assembly: AssemblyVersion("$four")]
"@
Set-Content "bootstrap\VersionInfo.cs" $vi -Encoding ASCII

# 3) Tiny native apply helper.
& $csc /nologo /target:winexe /optimize+ /reference:System.dll /reference:System.Core.dll `
    /out:bootstrap\ApplyHelper.exe bootstrap\ApplyHelper.cs bootstrap\VersionInfo.cs
if ($LASTEXITCODE -ne 0) { throw "csc failed building ApplyHelper" }

# 4) Launcher with the real app + helper embedded.
New-Item -ItemType Directory -Force -Path dist | Out-Null
& $csc /nologo /target:winexe /optimize+ /win32icon:playlimit.ico `
    /reference:System.dll /reference:System.Core.dll `
    /out:dist\PlayLimit.exe `
    /resource:$real,Payload /resource:bootstrap\ApplyHelper.exe,ApplyHelper `
    bootstrap\Launcher.cs bootstrap\VersionInfo.cs
if ($LASTEXITCODE -ne 0) { throw "csc failed building Launcher" }

$sha = (Get-FileHash dist\PlayLimit.exe -Algorithm SHA256).Hash
"$sha  PlayLimit.exe" | Set-Content dist\PlayLimit.exe.sha256 -NoNewline
$launcherVer = (Get-Item dist\PlayLimit.exe).VersionInfo.FileVersion

Write-Host ""
Write-Host "launcher: v$launcherVer, $((Get-Item dist\PlayLimit.exe).Length) bytes" -ForegroundColor Green
Write-Host "sha256:   $sha"
if ("$launcherVer".Trim() -ne $version) { throw "launcher version $launcherVer != version.txt $version" }
Write-Host "OK. Publish version.txt + version_info.txt + dist\PlayLimit.exe + dist\PlayLimit.exe.sha256." -ForegroundColor Green

exit 0
