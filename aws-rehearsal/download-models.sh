#!/usr/bin/env bash
# Mirrors step 2 of the DGX Spark guide: fetch the weights ONCE, while the box
# still has internet. After this, the engine runs with no route out.
set -euo pipefail
cd "$(dirname "$0")"
source .env && export HF_HOME

sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv
python3 -m venv ~/.hfenv
~/.hfenv/bin/pip install -q -U huggingface_hub

~/.hfenv/bin/hf download "$MODEL_PATH"

docker pull "$SGLANG_IMAGE"

# Print the digest. Paste it into compose.cpu.yml so the image can never
# change under you - this discipline is the point of the rehearsal.
docker inspect --format '{{index .RepoDigests 0}}' "$SGLANG_IMAGE"
