# CBWCHC deployment package

Everything needed to install the on-premises AI model and the fax application
on the CBWCHC server. Used alongside the **Guided Deployment — Session Runbook**.

**There is no Dockerfile for the model.** The model server is a pre-built image
pulled from Docker Hub. Only the values in `.env` change between environments.
The three application images (`ai`, `backend`, `web`) are built from Dockerfiles
that already exist in this repository and do not change.

## Files

| File | Purpose |
|---|---|
| `.env.example` | Copy to `.env`. The only file that differs between environments. |
| `download-models.sh` | Run once, before the session. ~27 GB of models and a 15 GB image. |
| `compose.model.yml` | The model server. Pre-built image; no build step. |
| `compose.secure.yml` | Isolation and the HTTPS gateway. Applied after the correctness check. |
| `templates/llm.conf.template` | Gateway configuration template. Never contains live keys. |
| `render-gateway-config.sh` | Inserts caller keys into the configuration at deploy time. |
| `correctness-check.sh` | 40 requests in batches of 8. Must report 0 wrong. |
| `compose.app.yml` | The fax application, pointed at the gateway. |

## Order

    ./download-models.sh                                    # before the session
    docker compose -f compose.model.yml up -d               # ~9 min first start
    ./correctness-check.sh                                  # 0 wrong out of 40
    ./render-gateway-config.sh
    docker compose -f compose.model.yml -f compose.secure.yml up -d
    docker compose -f ../../docker-compose.yml -f compose.app.yml up -d --build

## Corrections carried from the rehearsal

This package was built after executing the vendor guide end to end on a Linux
server. Four defects were found; all are corrected here.

1. **`map_hash_bucket_size`** — without it nginx refuses to start, with an error
   that does not indicate the cause. Fixed in `templates/llm.conf.template`.
2. **Keys inside a version-controlled file** — re-deploying silently replaced
   live keys with placeholders and locked every caller out, with no error.
   Keys now live in `keys/callers.txt`, outside source control.
3. **Fax folder ownership** — the containers run as UID 10001, not the host
   user. Without it the scanner fails silently. See the runbook, Phase 4.
4. **Ambiguous correctness check** — a model too small fails identically every
   time, which looks like an engine fault. Always follow a failure with a single
   isolated request. See the header of `correctness-check.sh`.

## Never commit

`.env`, `keys/callers.txt`, `conf.d/`, `tls/*.key`. All are gitignored.
