# On-prem LLM with SGLang — validated deployment procedure

The procedure below is run **identically on the laptop and on the client server**.
The only file that differs between environments is `local-llm/.env` (Stage 1) and
the root `.env` (Stage 2). No compose file is ever edited per-environment.

| | Laptop (validation) | Client server |
|---|---|---|
| GPU | RTX 4060, 8 GB | see client hardware sheet |
| `SGLANG_MODEL` | `Qwen/Qwen3.5-4B` | larger Qwen3.5 variant |
| `SGLANG_EXTRA_ARGS` | `--quantization fp8 --disable-cuda-graph` | empty |
| `SGLANG_CONTEXT` | `16384` | `32768` |
| `SGLANG_MEM` | `0.80` | `0.87` |
| `SGLANG_MAX_RUNNING` | `1` | `4` |
| `SGLANG_ALIAS` / `LLM_ALIAS` | `cwc-llm` | `cwc-llm` (unchanged — that is the point) |

The application never learns which model or engine is behind the endpoint. It
sends OpenAI-format requests to `http://llm:8080/v1` asking for the model named
`cwc-llm`, and that is all.

---

## Stage 0 — Prerequisites on the host

1. NVIDIA driver installed; `nvidia-smi` lists the GPU and shows it idle.
2. Docker Engine + Compose v2.
3. NVIDIA Container Toolkit, so containers can use the GPU.
4. Free disk: SGLang image ~10–15 GB, weights 8 GB+, allow 40 GB.
5. No other process holding GPU memory.

Verify the toolkit works before going further:

```
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

This must list the GPU. If it fails, nothing downstream will work — fix it here.

---

## Stage 1 — Model server alone, with internet

Stage 1 exists to download the weights and prove the model loads and answers.
It runs on a network that can reach Hugging Face. The weights land in the named
volume `local-llm_hf-cache`, which Stage 2 then mounts read-only.

```
cd local-llm
# .env: set LLM_API_KEY (new-api-key.ps1), SGLANG_MODEL, and the profile values
docker compose -f docker-compose.sglang.yml up -d
docker compose -f docker-compose.sglang.yml logs -f
```

Wait for `The server is fired up and ready to roll!`. First run includes the
weight download, so allow 15–40 minutes.

### Stage 1 acceptance tests

| # | Test | Expected |
|---|---|---|
| 1 | `curl http://localhost:30000/health` | 200 |
| 2 | `curl -H "Authorization: Bearer $KEY" http://localhost:30000/v1/models` | lists `cwc-llm` |
| 3 | `curl -i http://localhost:30000/v1/models` (no key) | **401** |
| 4 | chat completion with a one-word prompt | answers |
| 5 | chat completion with an image | describes the image |

Test 3 is the security control the client asked for; test 5 proves the vision
path, which the fax pipeline depends on.

Record: model load time, VRAM used at idle (`nvidia-smi`), SGLang image digest.

---

## Stage 2 — Full stack, model offline

Stage 2 runs the whole application with SGLang in place of llama.cpp. The `llm`
service sits on the internal network with **no route out**; `HF_HUB_OFFLINE=1`
makes a missing cache fail fast rather than hang on an impossible download.

```
cd ..
# root .env: LLM_API_KEY, LLM_ALIAS=cwc-llm, and the same SGLANG_* profile values
docker compose -f docker-compose.yml -f docker-compose.sglang.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.sglang.yml ps
```

All five services must reach `healthy`: `db`, `llm`, `ai`, `backend`, `web`.

### Stage 2 acceptance tests

| # | Test | Expected |
|---|---|---|
| 1 | All five containers `healthy` | yes |
| 2 | Model port probed from the host | refused — it is internal only |
| 3 | Web UI opens | yes |
| 4 | One fax dropped in Inbound | classified, renamed, routed; original archived |
| 5 | Full sample set run | accuracy + per-fax timing recorded |

---

## Results — fill in during the run

| Measurement | Laptop (Qwen3.5-4B, fp8) | Client server |
|---|---|---|
| SGLang image tag / digest | | |
| Model load time | | |
| VRAM at idle | | |
| VRAM under load | | |
| Seconds per fax (mean) | | |
| Routing accuracy vs manual | | |
| Docs held below the 0.90 gate | | |

Compare against the llama.cpp baseline already measured on this laptop:
Qwen3.5-9B Q4_K_M, 35/36 routing matches, ~88 s per fax.

**Note when reporting:** the laptop SGLang run uses a *smaller model at a
different quantisation* than the llama.cpp baseline. It validates the procedure,
the engine and the integration — not the accuracy of the model the client will
run. Those are separate claims and should stay separate in the client document.

---

## Known gaps to close before the client guide is final

- **SGLang on NVIDIA DGX Spark (GB10, `sm_121a`, ARM64) is not officially
  supported** — upstream tracking issue sgl-project/sglang#11658 is open. There
  is a dev-branch image tag, no stable ARM64 release, and FP8 kernels fall back
  to slower paths. If the client target is the DGX Spark, llama.cpp is the
  supported day-one engine and SGLang is a documented upgrade path.
- Pin `SGLANG_TAG` to a validated digest before shipping. Never ship `latest`.
- Confirm the chosen SGLang release supports the vision path for the client's
  model size, not only for the 4B.
