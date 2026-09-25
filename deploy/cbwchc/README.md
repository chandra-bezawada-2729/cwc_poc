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
| `app.sh` | Wrapper for the application stack. Use this, never a raw `docker compose`. |
| `export-feedback.sh` | Dumps the thumbs-up/down records and their summaries to CSV. |

## Order

    cp .env.example .env && chmod 600 .env                  # first, or nothing runs
    ./download-models.sh                                    # before the session
    docker compose -f compose.model.yml up -d               # ~9 min first start
    ./correctness-check.sh                                  # 0 wrong out of 40
    ./render-gateway-config.sh
    docker compose -f compose.model.yml -f compose.secure.yml up -d
    ./app.sh up -d --build                                  # the application

The last step uses `app.sh`, not a raw `docker compose`. The application's
compose file lives at the repository root while its overrides live here, so the
project directory and the `.env` location both have to be set explicitly.
`app.sh` does that; typing the compose command by hand does not, and it fails
with a missing-variable error that does not say why.

## Corrections carried from the rehearsal

This package was built after executing the vendor guide end to end on a Linux
server. Four defects were found in the vendor guide and one was introduced by
this package's own repackaging; all five are corrected here.

1. **`map_hash_bucket_size`** — without it nginx refuses to start, with an error
   that does not indicate the cause. Fixed in `templates/llm.conf.template`.
2. **Keys inside a version-controlled file** — re-deploying silently replaced
   live keys with placeholders and locked every caller out, with no error.
   Keys now live in `keys/callers.txt`, outside source control.
3. **Fax folder ownership** — the containers run as UID 10001, not the host
   user. Without it the scanner fails silently. See the runbook, Phase 4.
4. **Model directory not created by this package.** The vendor guide creates
   `/opt/models/hf` in its own step 1, which also sets up `/opt/qwen-spark`.
   This package replaces that step with a `git clone`, so nothing created the
   cache directory — `/opt` is root-owned and the download would have failed on
   a permission error after the CLI install. `download-models.sh` now creates
   it, takes ownership, and checks for the 85 GB the guide requires before
   starting a 42 GB transfer. This one is ours, not the vendor's.
5. **Ambiguous correctness check** — a model too small fails identically every
   time, which looks like an engine fault. Always follow a failure with a single
   isolated request. See the header of `correctness-check.sh`.

## Never commit

`.env`, `keys/callers.txt`, `conf.d/`, `tls/*.key`. All are gitignored.
