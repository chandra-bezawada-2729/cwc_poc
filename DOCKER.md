# Running the full stack in Docker

Five containers from one `docker-compose.yml`:

| Service | Image / build | Port | Network |
|---|---|---|---|
| `web` | `cwc-frontend/Dockerfile` (nginx + built React) | **127.0.0.1:8088** | internal + edge |
| `backend` | `cwc-backend/Dockerfile` (Spring Boot, Java 17) | 8080, not published | internal |
| `ai` | `cwc-ai-service/Dockerfile` (Python 3.11, Tesseract, waitress) | 5002, not published | internal |
| `llm` | `ghcr.io/ggml-org/llama.cpp:server-cuda` + Qwen3.5-9B | 8080, not published | internal |
| `db` | `postgres:17-alpine` | 5432, not published | internal |

`internal` has no route to the internet or the host. The fax folders are one host
folder (`C:\CWC_DOCKER` by default, deliberately **outside OneDrive** so faxes are never
synced to the cloud) mounted at `/data` in both `backend` and `ai`.

## First run on the laptop

1. **Secrets.** The root `.env` needs `CWC_DB_PASSWORD` and `LLM_API_KEY` (already added
   for this laptop; see `.env.example`).
2. **Free the GPU and the model.** Stop the stand-alone model server; the stack runs its own:
   ```powershell
   cd local-llm ; docker compose down ; cd ..
   ```
   Close other GPU apps (Epic Games Launcher, games).
3. **Check the model files are in the Docker volume** (downloaded earlier by `local-llm`):
   ```powershell
   docker run --rm -v local-llm_qwen-models:/models alpine sh -c "ls /models/models--unsloth--Qwen3.5-9B-GGUF/snapshots/*/"
   ```
   You should see `Qwen3.5-9B-Q4_K_M.gguf` and `mmproj-BF16.gguf`. If the snapshot folder
   name differs from the one in `docker-compose.yml`, set `LLM_MODEL_FILE` and
   `LLM_MMPROJ_FILE` in the root `.env` to the real paths.
4. **Fax folders:**
   ```powershell
   New-Item -ItemType Directory -Force C:\CWC_DOCKER\Inbound, C:\CWC_DOCKER\incoming, C:\CWC_DOCKER\Outbound
   ```
5. **Build and start** (first build downloads base images and dependencies: 10-20 min):
   ```powershell
   docker compose up -d --build
   docker compose ps
   ```
   Wait until `db`, `llm`, `ai`, `backend` and `web` all show `healthy`
   (`llm` takes about a minute to load the model; `backend` waits for `ai` and `db`).

## Check that everything talks to each other

```powershell
# Web and API through nginx (the only published port)
curl.exe -s http://localhost:8088/api/ingest/status

# AI service -> LLM, from inside the network
docker compose exec ai python -c "import urllib.request;print(urllib.request.urlopen('http://llm:8080/health').read())"

# AI service config: should say openai / qwen3.5-9b / http://llm:8080/v1
docker compose exec ai python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:5002/health').read())"

# The LLM is NOT reachable from the laptop (must fail; the local-llm stack is stopped)
curl.exe -s -m 3 http://localhost:8081/health
docker compose port llm 8080      # prints nothing: the port is not published

# Nothing inside can reach the internet (must fail)
docker compose exec ai python -c "import urllib.request;urllib.request.urlopen('https://huggingface.co', timeout=5)"
```

## End-to-end test

1. Open **http://localhost:8088**.
2. Copy one sample PDF into `C:\CWC_DOCKER\Inbound`.
3. Within ~15 s the scanner picks it up; about 60-90 s later it is filed under
   `C:\CWC_DOCKER\Outbound\<folder>\<new name>.pdf` and appears in the Inbox with its
   extracted fields. The original moves to `Inbound\_processed`.
4. Follow it live: `docker compose logs -f backend ai llm`

## Everyday commands

| Task | Command |
|---|---|
| Start / stop | `docker compose up -d` / `docker compose down` |
| Rebuild after code changes | `docker compose up -d --build` (or `--build ai` for one service) |
| Logs | `docker compose logs -f backend` |
| Reset the database (demo) | `docker compose down ; docker volume rm cwc_db-data ; docker compose up -d` and empty `C:\CWC_DOCKER\*` |
| Switch AUTO/SUGGEST | set `CWC_ROUTING_MODE=SUGGEST` in the root `.env`, then `docker compose up -d backend` |
| Back to the non-Docker setup | `docker compose down`, then `cd local-llm ; docker compose up -d` and run the services as before |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `llm` never healthy, log says model file not found | Snapshot path differs: set `LLM_MODEL_FILE` / `LLM_MMPROJ_FILE` (step 3) |
| `llm` exits with CUDA out of memory | Another model or GPU app is running; `nvidia-smi`, stop it, `docker compose up -d llm` |
| `backend` restarting, `Inbound path does not exist` | Create `C:\CWC_DOCKER\Inbound`, or check `CWC_DATA_DIR` |
| Every fax becomes UNKNOWN, AI log says file not found | `backend` and `ai` must mount the same folder at `/data` |
| `web` build fails in `npm ci` with a missing native binding | The fallback `npm install` runs automatically; if it still fails, delete `cwc-frontend/package-lock.json`, run `npm install` on the laptop, rebuild |
| Port 8088 in use | Set `WEB_PORT=8090` in the root `.env` |

## Moving this to a Linux VM (AWS)

Same files. On the VM: Docker + NVIDIA Container Toolkit, the root `.env` with new
secrets, `CWC_DATA_DIR=/srv/cwc-data`, and the model weights downloaded once into a
volume named `local-llm_qwen-models` (or change the `qwen-models` volume). To use SGLang
instead of llama.cpp, replace the `llm` service with the guide's SGLang block (port 30000)
and set `OPENAI_BASE_URL=http://llm:30000/v1` and the served model name for `ai`.
Put HTTPS (Caddy/nginx or a load balancer) in front of `web`; open only 443.
