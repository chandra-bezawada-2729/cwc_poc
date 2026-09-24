# AWS CPU rehearsal — DGX Spark deployment guide, dry run

Purpose: execute the **procedure** in the client's DGX Spark deployment guide on a
cheap, immediately available Linux VM, so the steps are proven before they are
performed on the client's hardware. No GPU, no quota request.

This rehearses the steps. It does **not** reproduce the client's engine
configuration or its performance — see "What does not transfer".

## Instance

| Setting | Recommended | Minimum |
|---|---|---|
| Instance type | `m7i.2xlarge` (8 vCPU, 32 GiB) | `m7i.xlarge` (4 vCPU, 16 GiB) |
| CPU generation | 4th-gen Intel Xeon (Sapphire Rapids) or newer | same |
| OS | Ubuntu 24.04 LTS | Ubuntu 22.04 LTS |
| Root volume | 100 GB gp3, encrypted | 60 GB |
| Security group | SSH 22 + HTTPS 443, office IP only | same |

The CPU generation is not optional: SGLang's CPU backend targets 4th-gen Xeon
or newer. `m5`, `m6i`, `t3` and the AMD `r7a`/`m7a` families will not serve.
`m7i` / `c7i` / `r7i` qualify. These use the ordinary standard-instance quota,
which accounts already have — no GPU quota, no support case.

## Step map — guide → rehearsal

| Guide step | Here | Changes |
|---|---|---|
| 1. Secrets file | same commands | paths under `/opt/qwen-aws` |
| 2. `download-models.sh` | `download-models.sh` | one model, no DFlash2 drafter |
| 3. `compose.spark.yml` | `compose.cpu.yml` | CPU flags (below) |
| 4. Start and watch | same | seconds to start, not ~9 minutes |
| 5. Test both APIs | same | `/v1/chat/completions`, 401 without key |
| 6. HTTPS gateway | `templates/llm.conf.template`, `compose.hipaa.yml` | **identical in substance** |
| Correctness check | `correctness-check.sh` | identical |

## Engine flags: what changed and why

Removed — Blackwell / DGX Spark only:

    --speculative-algorithm DFLASH            DFlash2 needs the Spark dev image
    --speculative-draft-model-path ...        (ARM64, sm_121a)
    --speculative-num-draft-tokens 8
    --speculative-draft-model-quantization unquant
    --kv-cache-dtype fp8_e4m3                 FP8 KV cache is a GPU feature
    --attention-backend flashinfer            CUDA kernel library
    --mem-fraction-static 0.50                guards unified memory; no GPU here
    --chunked-prefill-size / --cuda-graph-*   GPU scheduling
    --mamba-* / --enable-torch-compile        GPU-path tuning

Added — CPU backend:

    --device cpu
    --tp 1                                    the guide's --tp 6 matches a big
                                              Xeon's sub-NUMA clusters, not an
                                              8-vCPU VM
    --disable-overlap-schedule
    --trust-remote-code

Kept — and these are the ones that matter for the client: `--served-model-name`,
`--api-key`, `--context-length`, `--max-running-requests`, `--reasoning-parser`,
`--enable-metrics`, `--host/--port`.

## What DOES transfer

- The whole file layout and the order of operations
- Downloading weights once, then running the engine with no route out
  (`HF_HUB_OFFLINE=1`, `internal: true`)
- Pinning the image by digest
- The nginx gateway: per-user keys, both auth header styles, JSON audit log with
  no prompt text, `/v1/*` only and 404 on everything else, `proxy_buffering off`,
  `client_max_body_size 64m`, long read timeout
- `ports: !reset []` — proving the engine really is unreachable afterwards
- The concurrency correctness check
- Our AI service pointing at an OpenAI-compatible URL + key and not caring what
  is behind it

## What does NOT transfer

- **Speed.** CPU inference on a small model tells you nothing about the Spark.
  Do not carry any timing from here into a client document.
- **NVFP4 and DFlash2.** Blackwell hardware format and a Spark-specific image.
- **The unified-memory freeze risk.** A VM with no GPU cannot reproduce it, so
  `--mem-fraction-static 0.50` stays untested and must be respected on the
  client's box regardless.
- **Vision throughput.** A small model reading fax page images on CPU will take
  minutes per page. Fine for proving the path works; useless as a measurement.

## Run it

    sudo mkdir -p /opt/qwen-aws /opt/models/hf
    sudo chown -R "$USER" /opt/qwen-aws /opt/models/hf
    # copy this folder to /opt/qwen-aws, then:
    cd /opt/qwen-aws
    echo "API_KEY=$(openssl rand -hex 32)" > .env
    cat .env.example | grep -v '^API_KEY=' >> .env
    chmod 600 .env
    ./download-models.sh                 # paste the printed digest into compose.cpu.yml
    docker compose -f compose.cpu.yml up -d
    docker logs -f qwen-cpu
    ./correctness-check.sh               # must print "0 wrong"
    # then the gateway:
    #   generate tls/llm.crt + tls/llm.key, paste per-user keys into the template
    docker compose -f compose.cpu.yml -f compose.hipaa.yml up -d
