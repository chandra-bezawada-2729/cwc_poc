<#
    Checks every layer after install, reboot or upgrade. Exit code 0 = all passed.
      .\smoke-test.ps1                       # uses D:\cwc\llm\api-key.txt
      .\smoke-test.ps1 -WebUrl https://fax-ai.cbwchc.internal
#>
param(
    [string] $KeyFile = "D:\cwc\llm\api-key.txt",
    [string] $WebUrl  = ""
)
$fails = 0
function Check($name, [scriptblock] $test) {
    try { $r = & $test; Write-Host ("  PASS  {0}  {1}" -f $name, $r) -ForegroundColor Green }
    catch { $script:fails++; Write-Host ("  FAIL  {0}  {1}" -f $name, $_.Exception.Message) -ForegroundColor Red }
}
$key  = (Get-Content $KeyFile -Raw).Trim()
$body = '{"model":"qwen3.8-27b","max_tokens":16,"messages":[{"role":"user","content":"Reply with one word: ready"}],"chat_template_kwargs":{"enable_thinking":false}}'

Write-Host "Services" -ForegroundColor Cyan
foreach ($s in "cwc-llm","cwc-ai","cwc-backend") {
    Check $s { $st = (Get-Service $s).Status; if ($st -ne "Running") { throw "status $st" }; "$st" }
}
Write-Host "Model server (8081)" -ForegroundColor Cyan
Check "health" { (Invoke-RestMethod http://127.0.0.1:8081/health -TimeoutSec 10).status }
Check "answers with key" {
    (Invoke-RestMethod http://127.0.0.1:8081/v1/chat/completions -Method Post -TimeoutSec 120 `
        -Headers @{ Authorization = "Bearer $key" } -ContentType application/json -Body $body).choices[0].message.content.Trim()
}
Check "rejects without key" {
    try { Invoke-RestMethod http://127.0.0.1:8081/v1/chat/completions -Method Post -ContentType application/json -Body $body -TimeoutSec 30 | Out-Null; throw "accepted a request with no key" }
    catch { if ($_.Exception.Response.StatusCode.value__ -eq 401) { "401 as expected" } else { throw } }
}
Write-Host "AI service (5002)" -ForegroundColor Cyan
Check "health" {
    $h = Invoke-RestMethod http://127.0.0.1:5002/health -TimeoutSec 10
    if ($h.provider -ne "openai" -or $h.endpoint -notlike "http://127.0.0.1:8081*") { throw "points at $($h.provider) $($h.endpoint)" }
    "$($h.model) via $($h.endpoint)"
}
Write-Host "Backend (8080)" -ForegroundColor Cyan
Check "ingest status" { $s = Invoke-RestMethod http://127.0.0.1:8080/api/ingest/status -TimeoutSec 10; if (-not $s.enabled) { throw "folder watcher disabled" }; "watching $($s.inboundPath)" }
Check "routing config" { $c = Invoke-RestMethod http://127.0.0.1:8080/api/routing/config -TimeoutSec 10; "mode $($c.mode), gate $($c.autoRouteMinConfidence)" }
if ($WebUrl) {
    Write-Host "Web interface" -ForegroundColor Cyan
    Check "UI over HTTPS"  { (Invoke-WebRequest $WebUrl -UseBasicParsing -TimeoutSec 15).StatusCode }
    Check "API through IIS" { (Invoke-WebRequest "$WebUrl/api/ingest/status" -UseBasicParsing -TimeoutSec 15).StatusCode }
}
Write-Host "GPU" -ForegroundColor Cyan
Check "memory" { (& nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader) -join "; " }

if ($fails) { Write-Host "`n$fails check(s) failed." -ForegroundColor Red; exit 1 }
Write-Host "`nAll checks passed." -ForegroundColor Green
