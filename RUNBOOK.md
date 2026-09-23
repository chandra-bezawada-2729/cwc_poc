# CWC POC — local runbook

Everything on localhost. No Docker. All commands are **PowerShell**.

Four services, four terminals:

| # | Service | Port |
|---|---|---|
| T1 | AI service (Flask) | 5002 |
| T2 | Backend (Spring Boot) | 8080 |
| T3 | Frontend (Vite) | 3001 |
| T4 | Your terminal — psql, curl, file ops | — |

Postgres runs as a Windows service on 5432.

---

## Shell gotchas — read once

These cost real time the first go round:

| Don't | Do | Why |
|---|---|---|
| `mvn` | `.\mvnw.cmd` | Maven isn't on PATH; the repo has a wrapper |
| `curl` | `curl.exe` | `curl` is an alias for `Invoke-WebRequest`, which ignores `-X POST` |
| `pip` | `python -m pip` | Guarantees the same interpreter that runs the script |
| plain `python` | the venv's python | Dependencies live in `cwc-ai-service/venv` |

Also: `Get-NetTCPConnection -LocalPort 8080` **throws an error when the port is free.** That error is the all-clear, not a fault.

---

## One-time setup

Skip if already done.

```powershell
# 1. Postgres role + database
psql -U postgres -h localhost -c "CREATE ROLE cwc WITH LOGIN PASSWORD 'cwc_dev_password';"
psql -U postgres -h localhost -c "CREATE DATABASE cwc OWNER cwc;"

# 2. Folders
mkdir C:\CWC_TEST\Inbound, C:\CWC_TEST\Outbound

# 3. Local config — NOT committed, add to .gitignore
@"
cwc.ingestion.inbound.enabled=true
cwc.ingestion.inbound.path=C:/CWC_TEST/Inbound
cwc.storage.base-path=C:/CWC_TEST
cwc.storage.routed-dir=Outbound
"@ | Set-Content -Path "cwc-backend\src\main\resources\application-local.properties" -Encoding UTF8

# 4. Python deps (in the venv)
cd cwc-ai-service
.\venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..

# 5. Frontend deps
cd cwc-frontend
npm install
cd ..
```

Flyway creates the schema on first backend start — no migration step needed.

---

## Start

### T1 — AI service

```powershell
cd "C:\Users\ABC\OneDrive - AvenirDigital Solutions Pvt. Ltd (1)\Desktop\cwc_poc\cwc-ai-service"
.\venv\Scripts\python.exe server.py
```

Must be up **before** the backend. If it isn't, every fax classifies as UNKNOWN and lands in
Manual-Review — which looks like a classification failure rather than a missing service.

### T2 — Backend

```powershell
cd "C:\Users\ABC\OneDrive - AvenirDigital Solutions Pvt. Ltd (1)\Desktop\cwc_poc\cwc-backend"
$env:CWC_ROUTING_MODE = "AUTO"
.\mvnw.cmd spring-boot:run "-Dspring-boot.run.profiles=local"
```

**Both parts are mandatory.** Without the env var the mode falls back to `SUGGEST` and nothing
gets filed. Without the `local` profile the scanner never starts and the paths point at the repo's
own `storage/` folder instead of `C:\CWC_TEST`.

Confirm these six lines appear:

```
The following 1 profile is active: "local"
[STORAGE] Base path resolved: C:\CWC_TEST
[STORAGE] Routed directory ready: C:\CWC_TEST\Outbound
[SCANNER] Inbound root : C:\CWC_TEST\Inbound
[SCANNER] Archive      : C:\CWC_TEST\Inbound\_processed
[ROUTING] Mode resolved: AUTO (source: env CWC_ROUTING_MODE)
```

Missing `[SCANNER]` lines = profile not applied. `mode=SUGGEST` = env var not set.

### T3 — Frontend

```powershell
cd "C:\Users\ABC\OneDrive - AvenirDigital Solutions Pvt. Ltd (1)\Desktop\cwc_poc\cwc-frontend"
npm run dev
```

Open http://localhost:3001 — Inbox, Fax Detail, Review Queue, Routing Config, Accuracy.

### T4 — Your terminal

```powershell
$env:Path += ";C:\Program Files\PostgreSQL\17\bin"   # adjust version
$env:PGPASSWORD = "cwc_dev_password"
```

---

## Check everything is alive

```powershell
curl.exe http://127.0.0.1:5002/health
curl.exe http://localhost:8080/api/ingest/status
psql -U cwc -h localhost -d cwc -tAc "SELECT 'db ok'"
```

The status endpoint should report `"enabled": true` and the correct `inboundPath`.

---

## Run it

Copy faxes into `C:\CWC_TEST\Inbound` — Explorer drag-and-drop is fine, that's the real workflow.

```powershell
copy "C:\Users\ABC\Downloads\CWC_DATASET_PATH\*.pdf" C:\CWC_TEST\Inbound\
```

> Copy only the 17 PDFs at the top level. **Not** `Sample for Avenir Digital` — that's the
> ground-truth reference, not input.

Picked up within 15s automatically, or force it:

```powershell
curl.exe -X POST http://localhost:8080/api/ingest/scan
```

### Watch

```powershell
while($true){
  psql -U cwc -h localhost -d cwc -tAc "SELECT state, count(*) FROM cwc.ingest_ledger GROUP BY state ORDER BY state"
  Write-Host "---"
  Start-Sleep -Seconds 3
}
```

States run `DISCOVERED` → `INGESTED` → `FILED`. Four documents process concurrently.

---

## Check the result

```powershell
tree /F C:\CWC_TEST\Outbound
dir C:\CWC_TEST\Inbound                  # only _processed\ should remain
dir C:\CWC_TEST\Inbound\_processed\*     # originals, original names
```

```powershell
psql -U cwc -h localhost -d cwc -c "SELECT source_file_name, state, routed_folder, routed_file_name FROM cwc.ingest_ledger ORDER BY first_seen_at;"
```

Outbound files + Manual-Review count should equal the number of faxes you dropped.

---

## Reset between runs

```powershell
psql -U cwc -h localhost -d cwc -c "TRUNCATE cwc.ingest_ledger, cwc.routing_decisions, cwc.classification_results, cwc.extracted_metadata, cwc.fax_documents RESTART IDENTITY CASCADE;"
Remove-Item -Recurse -Force C:\CWC_TEST\Inbound\*, C:\CWC_TEST\Outbound\*, C:\CWC_TEST\incoming\* -ErrorAction SilentlyContinue
```

Clears the data, keeps the schema, so Flyway won't re-run. Do **both** — clearing only the
database leaves the old outbound tree and makes the next run's counts wrong.

---

## Stop

`Ctrl+C` in T1, T2, T3. If port 8080 is stuck:

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen | ForEach-Object {
  $p = Get-Process -Id $_.OwningProcess
  Write-Host "Stopping $($p.ProcessName) (PID $($p.Id))"
  Stop-Process -Id $p.Id -Force
}
```

---

## Evaluation (separate from a run)

```powershell
cd "C:\Users\ABC\OneDrive - AvenirDigital Solutions Pvt. Ltd (1)\Desktop\cwc_poc"
.\cwc-ai-service\venv\Scripts\python.exe evaluation\run_eval.py
```

Use the venv python, not global — same missing-module wall otherwise.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Files sit in Inbound, nothing happens | No `[SCANNER]` lines at startup — `local` profile not applied |
| Classified but nothing in Outbound | `mode=SUGGEST` — `CWC_ROUTING_MODE` not set in T2's terminal |
| Everything → Manual-Review | AI service down or no API key. Check `/health` first |
| All faxes ERRORED at OCR | Tesseract not on PATH |
| Outbound lands in `cwc_poc\storage\routed` | `local` profile not applied — base path fell back to the repo default |
| A file stays in Inbound and never moves to `_processed` | Its bytes are already in the ledger, so it was left in place rather than filed twice. This is the dedupe working, not a stall. Confirm by hashing it against the copy in `_processed` — same hash means it is already filed. Delete it, or reset to re-run |
| Upload rejected as `DUPLICATE` | Same content hash as a fax already received. The message names the folder and filename it was filed under |
| Inbox says "Status unavailable" | The frontend could not reach `/api/ingest/status` — backend down, or the Vite proxy is not running |
| Inbox says "Folder automation off" | `cwc.ingestion.inbound.enabled` is false. Correct when running without the `local` profile |
| Confidence column is blank | The fax has not been classified yet, or it predates the `calibratedConfidence` field being added to the Inbox projection |
| `ModuleNotFoundError` | Used global python instead of `.\venv\Scripts\python.exe` |
| `Port 8080 already in use` | Previous backend still running — see Stop |
| Startup fails "both true" | `folder-watch.enabled` and `inbound.enabled` both on. Intended guard |
| Backend exits on Flyway V7 | A failed V7 row is stuck in history — see **Flyway repair** below |
| Startup fails `Cannot load routing config` / `MalformedInputException` | The copy of a resource in `target\classes` is corrupt. Maven skips re-copying when size and mtime match the source, so it never repairs itself. Run with `clean`: `.\mvnw.cmd clean spring-boot:run "-Dspring-boot.run.profiles=local"` |
| Scan returns all zeros | Read `skippedKnown` / `candidates` in the response. Zeros with `candidates > 0` means the ledger already has those bytes, not a dead scanner |
| File Metadata card never fills | Extraction runs after the document reaches a resting state. If it stays on "Extracting…", check the AI service log for `[EXTRACT]` |

---

## Flyway repair

Two different startup failures, with two different causes. Read the message.

**`Migration checksum mismatch for migration version N`** — the migration applied
cleanly and the file was edited afterwards. The schema is already correct; only the
recorded fingerprint is stale.

**`Detected failed migration to version N`** — the migration threw partway. A
`success = false` row is left behind and Flyway refuses every later run until it is gone.

The same fix clears both, but only because every migration here is written to be
re-runnable (`ADD COLUMN IF NOT EXISTS`, `COMMENT ON`). Deleting a history row for a
migration that is NOT idempotent will try to re-apply it and fail on the second run.
Check the file before doing this.

```powershell
# 1. Look first. version, success and checksum are what matter.
psql -U cwc -h localhost -d cwc -c "SELECT installed_rank, version, description, checksum, success FROM cwc.flyway_schema_history ORDER BY installed_rank;"

# 2. Drop the row for the offending version — 7 in the example.
psql -U cwc -h localhost -d cwc -c "DELETE FROM cwc.flyway_schema_history WHERE version = '7';"
```

Restart the backend. The log should show `Migrating schema "cwc" to version 7` and then a
normal start. Confirm afterwards:

```powershell
psql -U cwc -h localhost -d cwc -c "SELECT version, success FROM cwc.flyway_schema_history WHERE version = '7';"
psql -U cwc -h localhost -d cwc -c "\d cwc.classification_results" | findstr evidence
```

`success` must be `t`, and `routing_evidence` and `naming_evidence` must both be listed.

Do not reach for `spring.flyway.validate-on-migrate=false`. It silences the check without
fixing anything, and the next real drift goes unnoticed.

---

## Tests

```powershell
# AI service — 60 tests
cd cwc-ai-service; .\venv\Scripts\python.exe -m pytest tests\ -q; cd ..

# Frontend — the evidence display filter
cd cwc-frontend; npm test; cd ..

# Backend
cd cwc-backend; .\mvnw.cmd -q test; cd ..
```

---

## AI cost per fax

Every classify and extract call now appends its exact token counts to
`cwc-ai-service\logs\ai_usage.jsonl`. This groups them by fax:

```powershell
.\cwc-ai-service\venv\Scripts\python.exe evaluation\cost_report.py
.\cwc-ai-service\venv\Scripts\python.exe evaluation\cost_report.py --since 2026-09-21
```

Writes `evaluation\reports\ai-cost-<stamp>.csv` with classify and extract
tokens and dollars per fax, repair retries counted. Only covers faxes processed
after the AI service was restarted with the tracker — earlier runs have no
per-fax record anywhere, the console included.

Prices live in `services\usage_tracker.py`. Override without editing the file:
`$env:CWC_PRICE_OPUS_4_5_INPUT = "5"`.

---

## Routing mapping document

Builds the CWC-vs-AI comparison table. Needs the AI service up; it does not
touch the database or the pipeline.

```powershell
.\cwc-ai-service\venv\Scripts\python.exe evaluation\build_mapping.py
.\cwc-ai-service\venv\Scripts\python.exe evaluation\build_mapping.py --only-manual
```

Writes CSV, XLSX and JSON into `evaluation\reports\`. `--only-manual` limits it
to the 36 documents in `Sample for Avenir Digital`, which are the only ones where
CWC's manual answer is known and an agreement rate means anything.

The 11 documents in the fax-line folders get `NEEDS CWC INPUT` in the manual
column — they are not in CWC's filed tree (no matching hash, size or page count)
so their manual routing has to come from CWC.

XLSX needs openpyxl: `.\cwc-ai-service\venv\Scripts\python.exe -m pip install openpyxl`

---

## Switches worth knowing

| Setting | Default | Effect |
|---|---|---|
| `CWC_ROUTING_MODE` | `SUGGEST` | `AUTO` files documents into the outbound tree. Set it in T2's terminal |
| `CWC_AUTO_EXTRACT` | `true` | Metadata extraction runs on its own once a document reaches a resting state. `false` returns to Re-Extract only |
| `auto-route-min-confidence` | `0.90` | In `routing-config.yml`. Below this a document is filed to Manual-Review instead of its predicted folder. Set to 0 to route on band alone |

Extraction is a second vision call and roughly doubles per-document latency. It runs
after filing (AUTO) or after the suggestion is persisted (SUGGEST), never before, so it
cannot delay or fail a route. A failed extraction leaves the document `ROUTED` and logs
a warning; `Re-Extract` on the Metadata tab is the retry.

---

## Known open items

- Two confidently-wrong routes from the Phase 7 run, still untriaged
- `FaxPipelineIntegrationTest` failing — likely the 4-arg `callClassify` Mockito stub against the
  5-arg production call, not pre-existing
- `storage/incoming` never self-cleans — every fax leaves a permanent copy with no retention policy
- Hydration branch never exercised against a real OneDrive placeholder
