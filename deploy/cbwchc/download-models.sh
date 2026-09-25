#!/usr/bin/env bash
# Part A of the deployment runbook. Run ONCE, before the session, while the
# server still has internet access. Takes 1-3 hours: about 27 GB of model
# files and a 15 GB container image.
#
# Run inside tmux. A dropped connection otherwise means starting again.
#
# Safe to re-run: hf download resumes a partial download, and docker pull
# skips layers it already has. If the link drops, just run it again.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Run this first:" >&2
  echo "    cp .env.example .env && chmod 600 .env" >&2
  exit 1
fi

set -a; source .env; set +a   # export everything, so HF_TOKEN reaches the CLI

: "${HF_HOME:?HF_HOME is not set in .env}"
: "${MODEL_PATH:?MODEL_PATH is not set in .env}"
: "${DRAFT_MODEL_PATH:?DRAFT_MODEL_PATH is not set in .env}"
: "${SGLANG_IMAGE:?SGLANG_IMAGE is not set in .env}"

# HF_HOME usually lives under /opt, which is root-owned. Create it and hand it
# to this user, or the first weight shard fails on a permission error.
if [[ ! -d "$HF_HOME" ]]; then
  echo "==> creating $HF_HOME"
  sudo mkdir -p "$HF_HOME"
  sudo chown -R "$(id -u):$(id -g)" "$HF_HOME"
fi
[[ -w "$HF_HOME" ]] || { echo "ERROR: $HF_HOME is not writable by $(whoami)" >&2; exit 1; }

# 42 GB of downloads plus extracted image layers. Find out now, not at 90%.
need_gb=85   # the vendor guide states ~85 GB free
have_gb=$(df -BG --output=avail "$HF_HOME" | tail -1 | tr -dc '0-9')
docker_gb=$(df -BG --output=avail /var/lib/docker 2>/dev/null | tail -1 | tr -dc '0-9' || echo 0)
echo "==> disk: ${have_gb}G free on $HF_HOME, ${docker_gb}G free for docker images"
if (( have_gb < need_gb )); then
  echo "ERROR: need about ${need_gb}G free for models and image layers." >&2
  echo "       Free space or point HF_HOME at a larger filesystem." >&2
  exit 1
fi

echo "==> installing the Hugging Face CLI"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv
python3 -m venv ~/.hfenv
~/.hfenv/bin/pip install -q -U huggingface_hub

echo "==> target model (~24 GB): $MODEL_PATH"
~/.hfenv/bin/hf download "$MODEL_PATH"
echo "==> draft model (~2.6 GB): $DRAFT_MODEL_PATH"
~/.hfenv/bin/hf download "$DRAFT_MODEL_PATH"

echo "==> model server image (~15 GB): $SGLANG_IMAGE"
docker pull "$SGLANG_IMAGE"

echo
echo "==> downloaded:"
du -sh "$HF_HOME"/hub/models--* 2>/dev/null || true

echo
echo "================ RECORD THIS ================"
docker inspect --format '{{index .RepoDigests 0}}' "$SGLANG_IMAGE"
echo "Put it in .env as SGLANG_IMAGE so the deployed version can never"
echo "change silently, and send it to the project team before the session."
echo "============================================="
