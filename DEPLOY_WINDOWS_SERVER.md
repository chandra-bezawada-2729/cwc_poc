# Deploying CWC to the CBWCHC Windows Server

Step-by-step for installing the whole application on CBWCHC's Windows Server once
the prerequisites (hardware, software list, accounts) from
`CBWCHC_OnPrem_AI_Prerequisites.docx` are in place. No Docker: every component runs
as a Windows service.

| Service | What | Listens on |
|---|---|---|
| `cwc-llm` | llama.cpp + Qwen3.8-27B on the GPU | 127.0.0.1:8081 (API key) |
| `cwc-ai` | AI service (OCR, prompts), waitress | 127.0.0.1:5002 |
| `cwc-backend` | Spring Boot backend + Inbound folder watcher | 127.0.0.1:8080 |
| `postgresql-x64-17` | Database | 127.0.0.1:5432 |
| IIS site | Web UI, HTTPS, proxies `/api` to 8080 | 0.0.0.0:443 |

Scripts live in `deploy/windows/`. Paths assume `D:\cwc` (software) and `D:\CWC_FAX`
(fax folders); change them consistently if CBWCHC uses other drives.

---

## Phase 0 - Before you go (our side, 1-2 weeks ahead)

1. **Confirm the prerequisites checklist** with CBWCHC IT (section 5 of the prerequisites doc):
   fax volume, server/GPU fitted, fax folder path, service account, DNS name + TLS
   certificate, VPN/RDP access, BitLocker on, download access or offline media.
2. **Freeze a version.** On the dev laptop: all tests green
   (`cwc-ai-service`: `python -m pytest -q tests`; backend: `.\mvnw.cmd test`;
   frontend: `npm run build; npm run lint; npm test`). Commit and tag it, e.g. `v1.0.0`.
3. **Build the release package:**
   ```powershell
   .\deploy\windows\build-release.ps1
   ```
   Produces `release\cwc-release-<stamp>.zip` + `.sha256`. No secrets are inside it.
4. **Assemble the install kit** (on encrypted media, or a folder CBWCHC downloads to the server):

   | Item | Where from |
   |---|---|
   | `cwc-release-<stamp>.zip` | step 3 |
   | llama.cpp Windows CUDA x64 zip + matching cudart zip | github.com/ggml-org/llama.cpp/releases (write down the version) |
   | `Qwen3.8-27B-UD-Q5_K_XL.gguf` (20.9 GB), `Qwen3.8-27B-UD-Q4_K_XL.gguf` (17.6 GB, fallback), `mmproj-F16.gguf` | huggingface.co/unsloth/Qwen3.8-27B-GGUF |
   | NVIDIA RTX Enterprise driver for Windows Server | nvidia.com/drivers |
   | VC++ 2015-2022 x64, Temurin JDK 17 MSI, Python 3.11 x64, Tesseract 5 (UB Mannheim), PostgreSQL 17, NSSM, IIS URL Rewrite + ARR | vendor sites |

   Record SHA-256 of every file (`Get-FileHash`) so you can prove what was installed.
5. **Rehearse once.** Do phases 2-8 on a spare Windows machine (your laptop is fine, with the
   9B model instead of 27B) without Docker. Anything that breaks there breaks on the day.
6. **Agree the day plan with CBWCHC IT:** who is on the call, change window, who can reboot,
   who holds the service-account password, and the go-live criteria in phase 9.

## Phase 1 - Prepare the server (CBWCHC IT, you watching)

1. Windows fully patched, BitLocker on, static IP, DNS name (e.g. `fax-ai.cbwchc.internal`).
2. Create folders:
   ```powershell
   New-Item -ItemType Directory -Force D:\cwc\tools, D:\cwc\llm\bin, D:\cwc\llm\models, D:\cwc\logs, D:\cwc\app
   New-Item -ItemType Directory -Force D:\CWC_FAX\Inbound, D:\CWC_FAX\incoming, D:\CWC_FAX\Outbound
   ```
3. Give the service account (e.g. `CBWCHC\svc-cwcfax`) Modify on `D:\CWC_FAX` and `D:\cwc\logs`,
   Read & Execute on `D:\cwc`. If the fax share is on another server, the account needs
   Modify there too, and use its UNC path in phase 5.
4. Antivirus: exclude `D:\cwc\llm\models` (large files scanned on every load).
5. Unzip the release to `D:\cwc\release\` and copy `nssm.exe` (64-bit) to `D:\cwc\tools\`.
6. Install the VC++ redistributable.

## Phase 2 - GPU and model server

1. Install the NVIDIA driver, reboot, then `nvidia-smi` must show the 32 GB card.
2. Unzip llama.cpp **and** the cudart zip into `D:\cwc\llm\bin` (the DLLs must sit next to
   `llama-server.exe`). Check: `D:\cwc\llm\bin\llama-server.exe --version`.
3. Copy the `.gguf` files into `D:\cwc\llm\models`.
4. API key:
   ```powershell
   cd D:\cwc\release\deploy\windows
   .\new-llm-key.ps1 -ServiceAccount "CBWCHC\svc-cwcfax"
   Copy-Item .\templates\llm-args.txt D:\cwc\llm\llm-args.txt
   ```
5. **Run it by hand first** (so errors are on screen, not in a service log):
   ```powershell
   cd D:\cwc\llm\bin
   .\llama-server.exe (Get-Content D:\cwc\llm\llm-args.txt -Raw).Trim().Split(' ')
   ```
   Wait for `server is listening`. In a second window run
   `D:\cwc\release\deploy\windows\smoke-test.ps1` - only the model-server checks matter now.
   `nvidia-smi` should show < ~30 GB used. **If it runs out of memory:** switch
   `llm-args.txt` to the Q4_K_XL file, or lower `-c 65536 --parallel 2` to `-c 32768 --parallel 1`.
   If a flag is rejected, check `llama-server.exe --help` for this version and fix `llm-args.txt`.
   Stop it with Ctrl+C.

## Phase 3 - Database

1. Install PostgreSQL 17 (service `postgresql-x64-17`), listen on localhost only
   (`listen_addresses = 'localhost'` in `postgresql.conf`).
2. Create the role and database (use a generated password; store it in CBWCHC's vault):
   ```powershell
   & "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -c "CREATE ROLE cwc LOGIN PASSWORD '<password>';"
   & "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -c "CREATE DATABASE cwc OWNER cwc;"
   ```
   Tables are created by the backend (Flyway) on first start.

## Phase 4 - AI service

1. Install Python 3.11 x64 (for all users) and Tesseract 5 to `C:\Program Files\Tesseract-OCR`.
2. Copy and install:
   ```powershell
   robocopy D:\cwc\release\cwc-ai-service D:\cwc\app\cwc-ai-service /E
   cd D:\cwc\app\cwc-ai-service
   py -3.11 -m venv venv
   .\venv\Scripts\pip install -r requirements.txt
   Copy-Item D:\cwc\release\deploy\windows\templates\ai-service.env .env
   notepad .env        # paste the key from D:\cwc\llm\api-key.txt into OPENAI_API_KEY
   icacls .env /inheritance:r /grant:r "Administrators:F" "SYSTEM:F" "CBWCHC\svc-cwcfax:R"
   ```
   No internet on the server? On a connected machine run
   `pip download -r requirements.txt -d wheels --platform win_amd64 --python-version 3.11 --only-binary=:all:`
   and install with `pip install --no-index --find-links wheels -r requirements.txt`.

## Phase 5 - Backend

1. Install Temurin JDK 17 (tick "set JAVA_HOME"). Check `java -version`.
2. Copy and configure:
   ```powershell
   New-Item -ItemType Directory -Force D:\cwc\app\cwc-backend\config
   Copy-Item D:\cwc\release\cwc-backend\cwc-backend.jar D:\cwc\app\cwc-backend\
   Copy-Item D:\cwc\release\deploy\windows\templates\application-prod.properties D:\cwc\app\cwc-backend\config\
   notepad D:\cwc\app\cwc-backend\config\application-prod.properties   # DB password, DNS name, fax paths
   icacls D:\cwc\app\cwc-backend\config\application-prod.properties /inheritance:r /grant:r "Administrators:F" "SYSTEM:F" "CBWCHC\svc-cwcfax:R"
   ```

## Phase 6 - Register and start the services

```powershell
cd D:\cwc\release\deploy\windows
.\install-services.ps1 -ServiceAccount "CBWCHC\svc-cwcfax" -RoutingMode SUGGEST
.\smoke-test.ps1
```
- The account needs "Log on as a service" (NSSM normally grants it; if a service will not start
  with error 1069, grant it in `secpol.msc` > Local Policies > User Rights Assignment).
- Logs: `D:\cwc\logs\cwc-llm.log`, `cwc-ai.log`, `cwc-backend.log` (rotated at 20 MB).
- Start order is enforced by dependencies: llm -> ai -> backend.

## Phase 7 - Web interface (IIS + HTTPS)

1. Add IIS (Web Server role), then install **URL Rewrite** and **Application Request Routing**.
   In IIS Manager > server > Application Request Routing Cache > Server Proxy Settings:
   tick **Enable proxy**, set **Time-out** to 600 seconds.
2. Copy `D:\cwc\release\web\*` to `D:\cwc\web` (includes `web.config`).
3. Create site `cwc-fax`, physical path `D:\cwc\web`, **https** binding on 443 with the
   CBWCHC certificate and the DNS name. Remove the http binding (or redirect it to https).
4. `.\smoke-test.ps1 -WebUrl https://fax-ai.cbwchc.internal` - the UI and API-through-IIS checks must pass.
5. Open the URL from a staff PC and log in / load the Inbox.

## Phase 8 - Firewall and hardening

```powershell
New-NetFirewallRule -DisplayName "CWC web (HTTPS)" -Direction Inbound -Protocol TCP -LocalPort 443 -RemoteAddress <staff subnet> -Action Allow
```
- Nothing else inbound: 8080/5002/8081/5432 already listen on 127.0.0.1 only.
- After install, outbound internet can be closed completely.
- Rotate anything typed during the install (DB password, LLM key) if it was shared over chat.

## Phase 9 - Acceptance test and go-live

1. **Reboot test:** restart the server; all services come back; `smoke-test.ps1` passes.
2. **Accuracy test** on the 36 CBWCHC-routed samples (copied to the server, never off it):
   ```powershell
   D:\cwc\app\cwc-ai-service\venv\Scripts\pip install requests openpyxl
   D:\cwc\app\cwc-ai-service\venv\Scripts\python D:\cwc\release\evaluation\build_mapping.py --dataset <samples folder> --only-manual --out D:\cwc\acceptance
   ```
   Pass mark: >= 35/36 folders match (the laptop pilot result). Note the average time per fax.
3. **End-to-end:** drop 5-10 real test faxes into `D:\CWC_FAX\Inbound`; each is filed and renamed
   under `Outbound`, shows in the Inbox, low-confidence ones land in Manual-Review; the original
   moves to `Inbound\_processed`.
4. **Go-live in SUGGEST mode** (AI proposes, staff confirm each fax) for 1-2 weeks, alongside the
   current manual process. Track how often staff change the AI's folder.
5. When CBWCHC signs off, switch to AUTO (faxes >= 90% confidence filed automatically):
   ```powershell
   D:\cwc\tools\nssm.exe set cwc-backend AppEnvironmentExtra CWC_ROUTING_MODE=AUTO
   Restart-Service cwc-backend
   ```
6. Point the fax server's output at `D:\CWC_FAX\Inbound` (CBWCHC IT).

## Phase 10 - Handover

- Give CBWCHC: this runbook, the install kit hashes, the llama.cpp and model versions,
  where secrets live (`api-key.txt`, `.env`, `application-prod.properties`), and the day-2 commands below.
- Remove or disable our remote access unless a support agreement (and BAA) covers it.
- Confirm backups: PostgreSQL (`pg_dump` nightly) and `D:\CWC_FAX\Outbound`.

---

## Rollback

The system never deletes or edits the original fax: the source stays in `Inbound\_processed`.
To roll back: `Stop-Service cwc-backend, cwc-ai, cwc-llm`, point the fax server back to its old
folder, and staff continue the manual process. To roll back an upgrade: keep the previous
release folder and jar, stop services, copy the old files back, start services.

## Day-2 operations

| Task | Command |
|---|---|
| Status | `Get-Service cwc-*` then `D:\cwc\release\deploy\windows\smoke-test.ps1` |
| Restart everything | `Restart-Service cwc-llm; Start-Sleep 60; Restart-Service cwc-ai, cwc-backend` |
| Logs | `Get-Content D:\cwc\logs\cwc-backend.log -Tail 100 -Wait` |
| GPU | `nvidia-smi` |
| Rotate the model key | `new-llm-key.ps1`, update `.env`, `Restart-Service cwc-llm, cwc-ai` |
| Upgrade the app | stop `cwc-backend`, `cwc-ai`; replace jar / AI service code / `web`; `pip install -r requirements.txt`; start; smoke test |
| Upgrade llama.cpp or the model | stop `cwc-llm`, replace files, edit `llm-args.txt`, start, smoke test, **re-run the accuracy test** |

## Known gaps to close before go-live

- If the model server is down, a fax currently becomes UNKNOWN and is filed, instead of waiting
  and retrying. Fix before AUTO mode.
- `routing-config.yml` (90% gate, folder rules) is inside the jar; changing it needs a new build.
- Thinking is disabled per request by the AI service (`enable_thinking: false`); keep it that way
  unless re-evaluated - it multiplies time per fax.
