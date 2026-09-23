<#
    Registers the three CWC components as Windows services with NSSM, in start order:
        cwc-llm      llama.cpp model server   127.0.0.1:8081
        cwc-ai       AI service (waitress)    127.0.0.1:5002   depends on cwc-llm
        cwc-backend  Spring Boot backend      127.0.0.1:8080   depends on cwc-ai + PostgreSQL
    Safe to re-run: an existing service is stopped and re-created.

      .\install-services.ps1 -ServiceAccount "CBWCHC\svc-cwcfax"
#>
param(
    [Parameter(Mandatory)] [string] $ServiceAccount,
    [string] $Root       = "D:\cwc",
    [string] $Nssm       = "D:\cwc\tools\nssm.exe",
    [string] $JavaExe    = "",
    [string] $PgService  = "postgresql-x64-17",
    [string] $RoutingMode = "SUGGEST"          # SUGGEST for the first weeks, AUTO after sign-off
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Nssm)) { throw "NSSM not found at $Nssm" }
if (-not $JavaExe) { $JavaExe = (Get-Command java -ErrorAction Stop).Source }
$cred = Get-Credential -UserName $ServiceAccount -Message "Password for the service account"
$pw   = $cred.GetNetworkCredential().Password
New-Item -ItemType Directory -Force "$Root\logs" | Out-Null

function Register-CwcService($name, $exe, $params, $dir, $depends, $envExtra) {
    if (Get-Service $name -ErrorAction SilentlyContinue) {
        & $Nssm stop $name confirm | Out-Null
        & $Nssm remove $name confirm | Out-Null
    }
    & $Nssm install $name $exe | Out-Null
    & $Nssm set $name AppParameters $params | Out-Null
    & $Nssm set $name AppDirectory $dir | Out-Null
    & $Nssm set $name AppStdout "$Root\logs\$name.log" | Out-Null
    & $Nssm set $name AppStderr "$Root\logs\$name.log" | Out-Null
    & $Nssm set $name AppRotateFiles 1 | Out-Null
    & $Nssm set $name AppRotateOnline 1 | Out-Null
    & $Nssm set $name AppRotateBytes 20971520 | Out-Null
    & $Nssm set $name AppExit Default Restart | Out-Null
    & $Nssm set $name AppRestartDelay 10000 | Out-Null
    & $Nssm set $name Start SERVICE_AUTO_START | Out-Null
    & $Nssm set $name ObjectName $ServiceAccount $pw | Out-Null
    if ($depends)  { & $Nssm set $name DependOnService @depends | Out-Null }
    if ($envExtra) { & $Nssm set $name AppEnvironmentExtra @envExtra | Out-Null }
    Write-Host "  registered $name" -ForegroundColor Green
}

Write-Host "[1/3] cwc-llm" -ForegroundColor Cyan
$llmArgs = (Get-Content "$Root\llm\llm-args.txt" -Raw).Trim()
Register-CwcService "cwc-llm" "$Root\llm\bin\llama-server.exe" $llmArgs "$Root\llm\bin" $null $null

Write-Host "[2/3] cwc-ai" -ForegroundColor Cyan
$ai = "$Root\app\cwc-ai-service"
Register-CwcService "cwc-ai" "$ai\venv\Scripts\python.exe" "server.py" $ai @("cwc-llm") @("PYTHONUNBUFFERED=1")

Write-Host "[3/3] cwc-backend" -ForegroundColor Cyan
$be = "$Root\app\cwc-backend"
$beArgs = "-Xms512m -Xmx2g -jar `"$be\cwc-backend.jar`" --spring.profiles.active=prod " +
          "--spring.config.additional-location=file:$($be -replace '\\','/')/config/"
Register-CwcService "cwc-backend" $JavaExe $beArgs $be @("cwc-ai", $PgService) @("CWC_ROUTING_MODE=$RoutingMode")

Write-Host "`nStarting services (the model takes about a minute to load)..." -ForegroundColor Cyan
Start-Service cwc-llm; Start-Sleep 60
Start-Service cwc-ai;  Start-Sleep 5
Start-Service cwc-backend
Get-Service cwc-* | Format-Table Name, Status, StartType
Write-Host "Next: .\smoke-test.ps1"
