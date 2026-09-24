#!/usr/bin/env bash
# Part A of the deployment runbook. Run ONCE, before the session, while the
# server still has internet access. Takes 1-3 hours: about 27 GB of model
# files and a 15 GB container image.
#
# Run inside tmux. A dropped connection otherwise means starting again.
set -euo pipefail
cd "$(dirname "$0")"
source .env && export HF_HOME

sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv
python3 -m venv ~/.hfenv
~/.hfenv/bin/pip install -q -U huggingface_hub

echo "==> target model (~24 GB)"
~/.hfenv/bin/hf download "$MODEL_PATH"
echo "==> draft model (~2.6 GB)"
~/.hfenv/bin/hf download "$DRAFT_MODEL_PATH"

echo "==> model server image (~15 GB)"
docker pull "$SGLANG_IMAGE"

echo
echo "================ RECORD THIS ================"
docker inspect --format '{{index .RepoDigests 0}}' "$SGLANG_IMAGE"
echo "Put it in .env as SGLANG_IMAGE so the deployed version can never"
echo "change silently, and send it to the project team before the session."
echo "============================================="
