# Rehearsal deployment: the Docker stack on an AWS GPU VM

Moves the stack that runs on the laptop (`DOCKER.md`) onto a Linux GPU VM, to rehearse the
client install and measure real performance. **Test data only** - synthetic or de-identified
faxes, never patient data.

VM: Ubuntu 24.04, g6.2xlarge (NVIDIA L4 24 GB), 200 GB encrypted disk, security group with
22 and 8088 open to the office IP only. See `Claude outputs/AWS_VM_Configuration_Request.docx`.

---

## Phase 0 - On the laptop, before you start

1. **Finish the local test first** (`DOCKER.md`): all five containers healthy and one fax
   processed end to end. Do not debug two environments at once.
2. **Commit and push** everything, including `docker-compose.yml`, `docker-compose.aws.yml`,
   the three Dockerfiles and `nginx.conf`. The VM pulls from the private repo.
3. Have ready: the SSH key (`cwc-rehearsal.pem`), the VM's public IP, and a GitHub
   **read-only deploy key** for the private repo (safer than your own credentials on a VM).

## Phase 1 - First login and host setup (about 15 minutes)

```bash
chmod 600 cwc-rehearsal.pem
ssh -i cwc-rehearsal.pem ubuntu@<vm-ip>

sudo apt-get update && sudo apt-get -y upgrade
nvidia-smi          # must list the L4; if "command not found", install the driver:
                    #   sudo apt-get install -y ubuntu-drivers-common && sudo ubuntu-drivers install
                    #   sudo reboot      (skip entirely on the Deep Learning GPU AMI)
```

Docker Engine + Compose:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER" && newgrp docker
docker compose version
```

NVIDIA Container Toolkit, so containers can use the GPU:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi   # must list the L4
```

## Phase 2 - Code and folders

```bash
# read-only deploy key for the private repo
ssh-keygen -t ed25519 -C "cwc-rehearsal-vm" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub      # add this in GitHub > repo > Settings > Deploy keys (read-only)

git clone git@github.com:<you>/cwc_poc.git ~/cwc_poc
sudo mkdir -p /srv/cwc-data/{Inbound,incoming,Outbound}
sudo chown -R "$USER:$USER" /srv/cwc-data
```

## Phase 3 - Secrets and settings

```bash
cd ~/cwc_poc
cat > .env <<'ENV'
CWC_DB_PASSWORD=<openssl rand -hex 24>
LLM_API_KEY=<openssl rand -hex 32, prefixed cwc->
CWC_DATA_DIR=/srv/cwc-data
WEB_PORT=8088
CWC_ROUTING_MODE=AUTO
LLM_CONTEXT=65536
LLM_PARALLEL=2
ENV
chmod 600 .env
```

Generate the two values with `openssl rand -hex 24` and `echo "cwc-$(openssl rand -hex 32)"`.
Never reuse the laptop's values.

## Phase 4 - Download the model into the Docker volume (once, about 10 minutes)

The stack's internal network has no route to the internet, so the weights are fetched
beforehand into the volume the `llm` container mounts.

```bash
docker volume create local-llm_qwen-models

docker run --rm -v local-llm_qwen-models:/models python:3.11-slim bash -c "
  pip install -q -U huggingface_hub &&
  hf download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf mmproj-BF16.gguf --local-dir /models"

docker run --rm -v local-llm_qwen-models:/models alpine ls -lh /models
# expect Qwen3.5-9B-Q4_K_M.gguf (~5.7 GB) and mmproj-BF16.gguf (~0.9 GB)
```

`docker-compose.aws.yml` points at exactly these two paths.

## Phase 5 - Build and start

```bash
cd ~/cwc_poc
docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.aws.yml ps
```

The first build takes 10-20 minutes (Maven, npm, pip). All five services must reach
`healthy`; `llm` takes about a minute to load the model.

Make the file pair the default so you can drop the `-f` flags:

```bash
echo 'COMPOSE_FILE=docker-compose.yml:docker-compose.aws.yml' >> ~/cwc_poc/.env
```

## Phase 6 - Check it

```bash
# API through nginx, from the VM
curl -s localhost:8088/api/ingest/status

# AI service -> model, inside the network
docker compose exec ai python -c "import urllib.request;print(urllib.request.urlopen('http://llm:8080/health').read())"
docker compose exec ai python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:5002/health').read())"

# the model port is not published anywhere
docker compose port llm 8080        # prints nothing
sudo ss -tlnp | grep -E '8080|5002|5432'   # only docker-proxy on 8088 should appear

# GPU is in use by the model
nvidia-smi
```

From your laptop: open `http://<vm-ip>:8088`. If it does not load, the security group is
the first thing to check.

## Phase 7 - Run faxes and measure

```bash
cp ~/samples/*.pdf /srv/cwc-data/Inbound/     # synthetic/de-identified only
docker compose logs -f backend ai
```

Record, for the client deployment plan:

- seconds per fax (`[CLS] Done ... latency=` and `[EXTRACT] done` in the AI service log)
- tokens/sec from the `llm` log
- whether two faxes at once (`LLM_PARALLEL=2`) improves throughput
- category and folder for each fax, against the expected result

For the 36-document accuracy run:

```bash
docker compose exec ai pip install -q requests openpyxl
docker compose exec ai python /app/evaluation/build_mapping.py --dataset /data/samples --only-manual --out /data/reports
```

(copy `evaluation/build_mapping.py` and the samples under `/srv/cwc-data` first, so the
container can see them).

## Phase 8 - Everyday use

| Task | Command (in `~/cwc_poc`) |
|---|---|
| Start / stop the stack | `docker compose up -d` / `docker compose down` |
| Update after a push | `git pull && docker compose up -d --build` |
| Logs | `docker compose logs -f backend ai llm` |
| Stop the VM overnight | `aws ec2 stop-instances --instance-ids <id>` (or the console) |
| After the VM restarts | `docker compose up -d` (containers do not auto-start after a stop/start) |

## Swapping llama.cpp for SGLang (the guide's engine)

Once the stack runs, this is the one-service change that rehearses the client engine:

1. Replace the `llm` service image and command with the SGLang block from the on-prem guide
   (`lmsysorg/sglang:latest`, port 30000, `--api-key`, `--reasoning-parser qwen3`), using a
   model that fits 24 GB.
2. Point the AI service at it: `OPENAI_BASE_URL=http://llm:30000/v1` and the matching
   `OPENAI_MODEL`.
3. Re-run phases 6 and 7 and compare the numbers with llama.cpp.

## Security notes for this VM

- Test data only. No PHI until there is a signed BAA and the client agrees.
- Only 22 and 8088 inbound, office IP only. The model, backend, AI service and database
  ports stay inside the VM.
- `8088` is plain HTTP. Fine for a rehearsal behind an IP allow-list; the client install
  uses HTTPS with their certificate.
- `.env` holds the database password and the model key: `chmod 600`, never committed.
- Terminate the VM when the rehearsal is over; the disk is encrypted but data is still data.
