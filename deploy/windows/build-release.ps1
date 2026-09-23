<#
    Builds a self-contained release package for a Windows Server install.
    Run on the DEVELOPMENT machine (needs JDK 17, Node 22, the repo), not the server.

      .\deploy\windows\build-release.ps1
      -> release\cwc-release-<stamp>.zip  (+ .sha256)

    Contents:  README-FIRST.txt, docs\        (prerequisites doc + runbook)
               cwc-backend\cwc-backend.jar   (Spring Boot fat jar)
               web\                          (built React UI + IIS web.config)
               cwc-ai-service\               (code, prompts, config; no venv, no .env, no logs)
               deploy\                       (these scripts + config templates)
    Secrets are never packaged: every .env / password comes from a template filled in on the server.
#>
param([string] $OutDir = (Join-Path $PSScriptRoot "..\..\release"))
$ErrorActionPreference = "Stop"
$root  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$rel   = Join-Path $OutDir "cwc-release-$stamp"
New-Item -ItemType Directory -Force $rel | Out-Null

Write-Host "[1/5] Backend jar" -ForegroundColor Cyan
Push-Location "$root\cwc-backend"
.\mvnw.cmd -q clean package -DskipTests
if ($LASTEXITCODE) { Pop-Location; throw "Maven build failed" }
Pop-Location
$jar = Get-ChildItem "$root\cwc-backend\target\*.jar" | Where-Object { $_.Name -notlike "*plain*" } | Select-Object -First 1
New-Item -ItemType Directory -Force "$rel\cwc-backend" | Out-Null
Copy-Item $jar.FullName "$rel\cwc-backend\cwc-backend.jar"

Write-Host "[2/5] Frontend" -ForegroundColor Cyan
# Build in a clean copy outside OneDrive. npm ci deletes node_modules first, which
# fails (EPERM) when the dev server or OneDrive sync holds files there - and it
# would wipe the node_modules you use for development.
$webBuild = Join-Path $env:TEMP "cwc-web-build"
if (Test-Path $webBuild) { Remove-Item $webBuild -Recurse -Force }
robocopy "$root\cwc-frontend" $webBuild /E /XD node_modules dist .vite /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy of the frontend failed ($LASTEXITCODE)" }
$global:LASTEXITCODE = 0
Push-Location $webBuild
npm ci --no-audit --no-fund
if ($LASTEXITCODE) { Pop-Location; throw "npm ci failed" }
npm run build
if ($LASTEXITCODE) { Pop-Location; throw "Frontend build failed" }
Pop-Location
Copy-Item "$webBuild\dist" "$rel\web" -Recurse
Copy-Item "$PSScriptRoot\templates\web.config" "$rel\web\web.config"
Remove-Item $webBuild -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[3/5] AI service (code only)" -ForegroundColor Cyan
robocopy "$root\cwc-ai-service" "$rel\cwc-ai-service" /E /XD venv logs tests __pycache__ .pytest_cache `
    /XF .env *.code-workspace /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
$global:LASTEXITCODE = 0
# Acceptance-test tool (compares AI folders with CBWCHC's manual routing)
New-Item -ItemType Directory -Force "$rel\evaluation" | Out-Null
Copy-Item "$root\evaluation\build_mapping.py" "$rel\evaluation\"

Write-Host "[4/5] Deploy scripts, templates and documents" -ForegroundColor Cyan
Copy-Item $PSScriptRoot "$rel\deploy" -Recurse
New-Item -ItemType Directory -Force "$rel\docs" | Out-Null
Copy-Item "$root\DEPLOY_WINDOWS_SERVER.md" "$rel\docs\"
foreach ($d in "CBWCHC_OnPrem_AI_Prerequisites.pdf", "CBWCHC_OnPrem_AI_Prerequisites.docx") {
    $src = Join-Path "$root\Claude outputs" $d
    if (Test-Path $src) { Copy-Item $src "$rel\docs\" }
}
$built = Get-Date -Format "yyyy-MM-dd HH:mm"
$commit = (git -C $root rev-parse --short HEAD 2>$null)
@"
CWC Fax Classification & Routing - installation package
Built: $built   Source commit: $commit

What is in this package
  cwc-backend\cwc-backend.jar     Backend (Spring Boot, Java 17). Runs as service cwc-backend.
  web\                            Web interface (built React app + IIS web.config).
  cwc-ai-service\                 AI service (Python 3.11): code, prompts, taxonomy config.
                                  Python packages are installed on the server from requirements.txt.
  evaluation\build_mapping.py     Acceptance test: compares AI routing with CBWCHC's manual routing.
  deploy\windows\                 Install scripts (API key, Windows services, smoke test)
  deploy\windows\templates\       Config templates to fill in on the server (no secrets inside).
  docs\                           Prerequisites document and the step-by-step deployment runbook.

NOT in this package (download on the server or bring on encrypted media - see the
prerequisites document, section 4 and step 3):
  - llama.cpp Windows CUDA build + CUDA runtime DLLs
  - Qwen3.8-27B model files (about 22 GB)
  - Installers: NVIDIA driver, JDK 17, Python 3.11, PostgreSQL, Tesseract, NSSM, IIS modules
  - Any password, API key or .env file (created on the server)

Start with: docs\DEPLOY_WINDOWS_SERVER.md
"@ | Set-Content "$rel\README-FIRST.txt" -Encoding utf8

$leaks = Get-ChildItem $rel -Recurse -Force -Include ".env", "application-local.properties", "*.log", "api-key.txt" -ErrorAction SilentlyContinue
if ($leaks) { throw "Refusing to package secrets/logs: $($leaks.FullName -join ', ')" }

Write-Host "[5/5] Zip + checksum" -ForegroundColor Cyan
Compress-Archive -Path "$rel\*" -DestinationPath "$rel.zip" -Force
$hash = (Get-FileHash "$rel.zip" -Algorithm SHA256).Hash
Set-Content "$rel.zip.sha256" $hash
Write-Host "`nRelease: $rel.zip`nSHA-256: $hash" -ForegroundColor Green
