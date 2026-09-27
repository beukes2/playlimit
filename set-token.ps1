# Save the GitHub log-shipping token on this machine so logs upload automatically.
# Run via set-token.bat (double-click). The token is typed in masked and stored
# only as C:\ProgramData\AlbionLimiter\github_token.txt - never in this repo.
$ErrorActionPreference = "Stop"

$tokenFile = "C:\ProgramData\AlbionLimiter\github_token.txt"

Write-Host "PlayLimit - log shipping token setup" -ForegroundColor Green
Write-Host ""
Write-Host "Get a token first:"
Write-Host "  GitHub -> Settings -> Developer settings -> Personal access tokens"
Write-Host "  -> Fine-grained tokens -> Generate new token"
Write-Host "  Resource owner: beukes2"
Write-Host "  Repository access: Only select repositories -> playlimit-logs"
Write-Host "  Permissions: Contents = Read and write"
Write-Host "  Expiration: your choice (write the date down to rotate later)"
Write-Host ""
Write-Host "If a token file already exists it will be replaced." -ForegroundColor Yellow
Write-Host ""

$sec = Read-Host -AsSecureString "Paste the token, then press Enter (input is hidden)"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$token = ($token -replace "\s", "")
if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Host "No token entered - nothing changed." -ForegroundColor Red
    exit 1
}

try {
    New-Item -ItemType Directory -Force -Path (Split-Path $tokenFile) | Out-Null
    Set-Content -Path $tokenFile -Value $token -NoNewline -Encoding ASCII
} catch {
    Write-Host "Could not write $tokenFile" -ForegroundColor Red
    Write-Host "Try running this as Administrator." -ForegroundColor Yellow
    Write-Host $_.Exception.Message
    exit 1
}

Write-Host ""
Write-Host ("Token saved ($($token.Length) chars) -> $tokenFile") -ForegroundColor Green
Write-Host "Restart PlayLimit (or press Ctrl+Shift+D then start it again) and the"
Write-Host "logs will upload to: https://github.com/beukes2/playlimit-logs/tree/main/logs"
Write-Host ""
Write-Host "You can verify in a minute with:"
Write-Host '  Select-String -Path C:\ProgramData\AlbionLimiter\limiter.log -Pattern "LogShip"'
