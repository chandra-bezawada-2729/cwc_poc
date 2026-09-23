# CWC Healthcare Fax Classification & Routing — POC

> **IMPORTANT — PHI Notice**
> This POC processes real patient fax documents. It is **not** HIPAA-compliant hosting.
> CWC data must stay on approved infrastructure. Do not push the `storage/` folder,
> real PDF files, or `.env` files to any repository.

---

## What this is

A three-service POC that classifies inbound healthcare faxes with Claude vision AI and
routes them to the correct destination folder, with a human-in-the-loop review step.

```
React UI  :3001  →  Spring Boot API  :8080  →  Flask AI Service  :5002
                           │
                     PostgreSQL 16  :5432  (native, primary)
```

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Java JDK | 17 | `java -version` |
| Maven | 3.9+ | **bundled** via `.\mvnw.cmd` — downloaded automatically, no install needed |
| Node.js | 20+ | `node -v` |
| Python | 3.11 | `python --version` |
| Tesseract OCR | 5.x | See below for Windows install |
| PostgreSQL | 15+ | Native install (primary path). Docker Desktop is an optional fallback — see below |

### Install Tesseract on Windows

Download from the [UB Mannheim builds](https://github.com/UB-Mannheim/tesseract/wiki).
Install to any folder; add it to your **system PATH** so `tesseract --version` works.
Do **not** hardcode the path anywhere in source.

---

## Setup (Windows PowerShell)

### 0. Set JAVA_HOME (required for the Maven wrapper)

```powershell
# Adjust path if your JDK is in a different location
$env:JAVA_HOME = "C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
```

To make this permanent, set it in System → Advanced → Environment Variables.

### 1. Copy environment files

```powershell
Copy-Item .env.example .env
Copy-Item cwc-ai-service\.env.example cwc-ai-service\.env
```

Edit **both** `.env` files and set at minimum:
- `ANTHROPIC_API_KEY` — your Anthropic API key

### 2. Database setup (native PostgreSQL — primary path)

This project uses a native PostgreSQL instance. The commands below create the `cwc`
role and database alongside any existing databases without touching them.

```powershell
$PG = "C:\Program Files\PostgreSQL\17\bin\psql.exe"   # adjust version if needed
$env:PGPASSWORD = "<your postgres superuser password>"

# Create the cwc role and database
& $PG -U postgres -h localhost -p 5432 -c "CREATE ROLE cwc WITH LOGIN PASSWORD 'cwc_dev_password';"
& $PG -U postgres -h localhost -p 5432 -c "CREATE DATABASE cwc OWNER cwc;"

# Verify the cwc role can log in — should print "cwc | cwc"
$env:PGPASSWORD = "cwc_dev_password"
& $PG -U cwc -h localhost -p 5432 -d cwc -c "SELECT current_database(), current_user;"

$env:PGPASSWORD = ""   # clear
```

> **Alternative — Docker Compose (optional fallback)**
> If you do not have a native PostgreSQL installation, `docker-compose.db-only.yml` provides
> a Docker container on port **5435** (not 5432 — see comments inside the file).
> If you use Docker, update `DB_URL` in `.env` to use port 5435.

Flyway runs automatically when the backend starts and creates the `cwc` schema.

### 3. Start the Spring Boot backend

```powershell
cd cwc-backend
.\mvnw.cmd spring-boot:run
```

The first run downloads Maven 3.9.9 into `~/.m2/wrapper/dists` automatically.
Subsequent runs use the cached copy and are fast.

Backend is ready when you see `Started CwcApplication`.

> **Note — timezone:** The Spring Boot Maven plugin is configured with
> `-Duser.timezone=UTC`. If you run the packaged JAR directly, set:
> ```powershell
> $env:JAVA_TOOL_OPTIONS = "-Duser.timezone=UTC"
> java -jar cwc-backend/target/cwc-backend-*.jar
> ```

### 4. Start the Flask AI service

```powershell
cd cwc-ai-service
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python server.py
```

AI service is ready when you see `CWC AI service starting on port 5002`.

### 5. Start the React frontend

```powershell
cd cwc-frontend
npm install
npm run dev
```

Frontend is ready when you see `Local: http://localhost:3001/`.

---

## Verify Phase 1

Run these four checks. All should return without error.

```powershell
# 1. PostgreSQL (native — no Docker needed)
$PG = "C:\Program Files\PostgreSQL\17\bin\psql.exe"
$env:PGPASSWORD = "cwc_dev_password"
& $PG -U cwc -h localhost -p 5432 -d cwc -c "\dt cwc.*"
$env:PGPASSWORD = ""
# Expected: fax_documents, classification_results, routing_decisions,
#           flyway_schema_history (4 rows)

# 2. Spring Boot backend
Invoke-RestMethod http://localhost:8080/api/health | ConvertTo-Json
# Expected: { "status": "UP", "service": "cwc-backend", ... }

# 3. Flask AI service
Invoke-RestMethod http://localhost:5002/health | ConvertTo-Json
# Expected: { "status": "UP", "service": "cwc-ai-service",
#             "provider": "anthropic", "model": "...", "tesseract_version": "..." }

# 4. React frontend
Start-Process "http://localhost:3001/inbox"
# Expected: browser opens showing the CWC Fax sidebar with Inbox selected
```

---

## Project layout

```
cwc_poc/
├── docker-compose.yml       full stack in Docker (see DOCKER.md)
├── docker-compose.db-only.yml  optional fallback — Docker PostgreSQL on port 5435
├── .env.example             root env vars (DB_URL, shared config)
├── cwc-backend/             Spring Boot 3.2 / Java 17 / Maven — port 8080
├── cwc-ai-service/          Flask 3 / Python 3.11 — port 5002
├── cwc-frontend/            React 18 / Vite / TypeScript — port 3001
├── evaluation/
│   ├── golden_set.yml       hand labels for 5 real samples
│   ├── fixtures/            recorded responses for --dry-run offline demos
│   └── run_eval.py          accuracy harness (Phase 7)
└── storage/
    ├── incoming/            faxes land here on upload or folder-watch
    └── routed/              routing destinations (one folder per category)
```

---

## Build phases

| Phase | What gets built | Status |
|---|---|---|
| **1** | Scaffold — services start, Flyway runs, /health green | ✅ Done |
| 2 | Upload, ingestion, storage, Inbox list | — |
| 3 | OCR + page rendering | — |
| 4 | Vision classification (Claude) | — |
| 5 | Calibrated confidence + gate | — |
| 6 | Routing engine, SUGGEST mode, confirm/override | — |
| 7 | Golden-set eval harness + Accuracy page | — |
| 8 | Dashboard, CSV export, folder watcher, AUTO mode, polish | — |

---

---

## 10-Minute Demo Script (cold-start to routed)

This reproduces the entire demo end-to-end. Assumes all three services are already running
(follow the Setup section above). Run every command in PowerShell from the repo root.

### Step 0 — Verify everything is up

```powershell
Invoke-RestMethod http://localhost:8080/api/health  | ConvertTo-Json  # backend UP
Invoke-RestMethod http://localhost:5002/health      | ConvertTo-Json  # AI service UP
# Browser: http://localhost:3001/inbox — should show Inbox with 0 faxes (or prior test data)
```

### Step 1 — Upload the 5 sample faxes (2 min)

```powershell
$DS = "C:\path\to\cwc healthcare datasets"   # set to your actual CWC_DATASET_PATH

python - <<'PY'
import requests, glob, os, sys
ds = os.environ.get("CWC_DATASET_PATH") or sys.argv[1]
pdfs = glob.glob(os.path.join(ds, "*.pdf"))
r = requests.post(
    "http://localhost:8080/api/faxes/upload",
    files=[("files", open(p, "rb")) for p in pdfs]
)
for u in r.json()["uploaded"]:
    print(f"  queued: {u['trackingId']}  {u['originalFileName']}")
PY
```

Watch the Inbox page poll: faxes move RECEIVED → OCR → CLASSIFYING → CLASSIFIED in ~15–30 s each.

### Step 2 — Inspect a classified fax (1 min)

In the browser, click any fax row → Fax Detail page:
- PDF preview on left
- Right panel: category, subtype, calibrated confidence badge, evidence quotes
- Routing suggestion with `[Confirm Routing]` button and override dropdown

### Step 3 — Confirm a routing suggestion (1 min)

Click `[Confirm Routing]` on the Payer Care Gap fax.
The routing status changes to **Routed**. Verify the file on disk:

```powershell
Get-ChildItem .\storage\routed\Care-Gaps\
# Should show the PDF with a UUID prefix
```

### Step 4 — Override one routing suggestion (1 min)

On the radiology fax (Follow-Up) detail page:
1. Select a different folder from the dropdown (e.g., `Manual-Review`)
2. The reason field becomes required — type `Wrong folder for demo`
3. Click `[Confirm Routing]` → status becomes **Overridden**

Check the review queue:
```powershell
Invoke-RestMethod http://localhost:8080/api/review-queue | ConvertTo-Json -Depth 2
# Overridden-to-manual-review fax now appears here
```

### Step 5 — Flip to AUTO mode and watch a file self-route (2 min)

```powershell
# Switch to AUTO mode (in memory only — YAML unchanged)
Invoke-RestMethod http://localhost:8080/api/routing/mode -Method Post `
  -Body '{"mode":"AUTO"}' -ContentType "application/json"

# Upload one more fax
python -c "import requests; print(requests.post('http://localhost:8080/api/faxes/upload', files=[('files', open(r'$DS\(614)321-2042_2026-08-18_1004PM.pdf','rb'))]).json())"

# Wait ~30 s, then check — it should be ROUTED with no human click
Start-Sleep 35
# Look up the most recent fax tracking ID from the Inbox and check:
Invoke-RestMethod http://localhost:8080/api/faxes -Method Get | Select-Object -ExpandProperty content | Select-Object -First 1 | ConvertTo-Json
```

Verify the file landed in `storage/routed/Prior-Authorization/`:
```powershell
Get-ChildItem .\storage\routed\Prior-Authorization\
```

Reset mode after demo:
```powershell
Invoke-RestMethod http://localhost:8080/api/routing/mode -Method Post `
  -Body '{"mode":"SUGGEST"}' -ContentType "application/json"
```

### Step 6 — Run the accuracy harness (1 min)

```powershell
python evaluation/run_eval.py   # auto-loads CWC_DATASET_PATH from cwc-ai-service/.env
# Or pass the path explicitly:
python evaluation/run_eval.py --datasets-path "$DS"
```

Then open **http://localhost:3001/accuracy** — the report renders with:
- Category/subtype/folder accuracy
- Gate quality table (auto-routable-and-wrong must be 0)
- Confusion matrix
- Per-file evidence quotes

### Step 7 — Export audit log (30 s)

```powershell
Invoke-RestMethod http://localhost:8080/api/audit/export.csv -OutFile audit.csv
Import-Csv .\audit.csv | Format-Table
```

### Dashboard quick-look

The Inbox header row shows live stats: total faxes, classified, routed, override rate,
avg classification latency, and action-required items due within 7 days.

---

## 16.5a Note — Packaged JAR run (STORAGE_BASE)

When running the packaged JAR instead of `mvn spring-boot:run`, the
`cwc.storage.base-path` default (`../storage`) resolves against the CWD of the JVM
process. Set `STORAGE_BASE` explicitly to avoid storing files in unexpected locations:

```powershell
$env:JAVA_TOOL_OPTIONS = "-Duser.timezone=UTC"
$env:STORAGE_BASE      = "C:\absolute\path\to\cwc_poc\storage"   # ← set this
java -jar cwc-backend/target/cwc-backend-*.jar
```

---

## Stopping services

```powershell
# Spring Boot / Flask / Vite — Ctrl+C in each terminal

# If using the Docker Postgres fallback:
docker compose -f docker-compose.db-only.yml down
# If running the full stack in Docker (DOCKER.md):
docker compose down
```

---

## Security notes

- Secrets come from `.env` files only. `.env` is in `.gitignore`. Never commit it.
- `storage/` and `evaluation/reports/` are in `.gitignore`. Never commit routed faxes.
- The Anthropic API call sends fax images (which contain PHI) to a third-party cloud
  service. Confirm BAA status with CWC before processing real patient data.
