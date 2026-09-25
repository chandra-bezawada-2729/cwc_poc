#!/usr/bin/env bash
# Run this on the server BEFORE the deployment session, and before
# download-models.sh. Every check here failed or nearly failed somewhere
# during preparation. It changes nothing; it only reports.
#
#   ./preflight.sh
#
# Anything marked FAIL must be fixed before the session. The fix is printed
# with the failure.
set -uo pipefail
cd "$(dirname "$0")"

fails=0
ok()   { echo "  PASS  $*"; }
bad()  { echo "  FAIL  $*"; fails=$((fails+1)); }
note() { echo "        $*"; }

echo "== 1. Docker and Compose =="
if command -v docker >/dev/null; then
  ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"
else
  bad "docker not installed"; note "install docker.io and docker-compose-v2"
fi
cv=$(docker compose version --short 2>/dev/null || echo 0)
if [ "$(printf '%s\n2.24.4\n' "$cv" | sort -V | head -1)" = "2.24.4" ]; then
  ok "docker compose $cv (needs 2.24.4+ for !reset in compose.secure.yml)"
else
  bad "docker compose $cv is too old"; note "needs 2.24.4+; upgrade docker-compose-v2"
fi
if docker ps >/dev/null 2>&1; then
  ok "this user can run docker without sudo"
else
  bad "cannot run docker as $(whoami)"
  note "sudo usermod -aG docker \$USER   then log out and back in"
fi

echo
echo "== 2. GPU visible INSIDE a container =="
# The host driver working is not the same thing. DGX OS ships
# no-cgroups = true, which lets the container see the NVIDIA stack but
# denies it the device nodes. It surfaces as an unhelpful NVML error.
if ! command -v nvidia-smi >/dev/null || ! nvidia-smi >/dev/null 2>&1; then
  bad "nvidia-smi fails on the host - the driver itself is not working"
  note "this is a host/driver problem; the site administrator must fix it"
elif docker run --rm --gpus all ubuntu nvidia-smi >/dev/null 2>&1; then
  ok "container can reach the GPU"
else
  bad "host driver works but a container cannot reach the GPU"
  note "Failed to initialize NVML: Unknown Error is the usual symptom."
  if grep -q 'no-cgroups *= *true' /etc/nvidia-container-runtime/config.toml 2>/dev/null; then
    note "CAUSE FOUND: no-cgroups = true in /etc/nvidia-container-runtime/config.toml"
    note "  docker info | grep -i rootless      # must print nothing first"
    note "  sudo nvidia-ctk config --in-place --set nvidia-container-cli.no-cgroups=false"
    note "  sudo systemctl restart docker"
  else
    note "  sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
  fi
fi

echo
echo "== 3. Disk =="
for p in "${HF_HOME:-/opt/models/hf}" /var/lib/docker; do
  d=$p; while [ ! -d "$d" ] && [ "$d" != "/" ]; do d=$(dirname "$d"); done
  g=$(df -BG --output=avail "$d" 2>/dev/null | tail -1 | tr -dc '0-9')
  if [ "${g:-0}" -ge 85 ]; then ok "$p: ${g}G free"; else bad "$p: only ${g}G free, need 85G"; fi
done

echo
echo "== 4. Reaching the download hosts =="
for h in github.com huggingface.co cas-bridge.xethub.hf.co registry-1.docker.io; do
  if curl -sS --max-time 10 -o /dev/null -w '' "https://$h" 2>/dev/null; then
    ok "$h"
  else
    bad "$h unreachable"
    note "the network team must allow this host, or set HTTP_PROXY/HTTPS_PROXY"
  fi
done
note "xethub is where Hugging Face serves large files; allowing only"
note "huggingface.co gives a download that authenticates and then dies."

echo
echo "== 5. The engine image matches this machine's architecture =="
arch=$(uname -m)
if [ -f .env ]; then
  img=$(grep -E '^SGLANG_IMAGE=' .env | cut -d= -f2-)
  if docker manifest inspect "$img" 2>/dev/null | grep -q 'arm64\|aarch64'; then
    ok "$img publishes arm64 (this machine is $arch)"
  else
    bad "could not confirm an arm64 build of $img"
    note "this machine is $arch; an amd64-only image will not run"
  fi
else
  bad ".env not found"; note "cp .env.example .env && chmod 600 .env"
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "All checks passed. Next: ./download-models.sh   (run it inside tmux)"
else
  echo "$fails check(s) failed. Fix them before the session."
fi
exit "$fails"
