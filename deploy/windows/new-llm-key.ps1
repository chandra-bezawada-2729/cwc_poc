<#
    Creates the API key the AI service uses to call the local model server,
    stored in D:\cwc\llm\api-key.txt readable only by Administrators and the
    service account. Re-run to rotate; then update OPENAI_API_KEY in the AI
    service .env and restart cwc-llm and cwc-ai.

      .\new-llm-key.ps1 -ServiceAccount "CBWCHC\svc-cwcfax"
#>
param(
    [Parameter(Mandatory)] [string] $ServiceAccount,
    [string] $KeyFile = "D:\cwc\llm\api-key.txt"
)
$ErrorActionPreference = "Stop"
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$key = "cwc-" + (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
New-Item -ItemType Directory -Force (Split-Path $KeyFile) | Out-Null
Set-Content -Path $KeyFile -Value $key -NoNewline -Encoding ascii
icacls $KeyFile /inheritance:r /grant:r "Administrators:F" "SYSTEM:F" "${ServiceAccount}:R" | Out-Null
Write-Host "Key written to $KeyFile (Administrators + $ServiceAccount only)."
Write-Host "Paste the file's contents into OPENAI_API_KEY in the AI service .env."
