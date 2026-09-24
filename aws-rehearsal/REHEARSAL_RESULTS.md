# AWS CPU rehearsal — results

Run: 24 September 2026
Host: AWS EC2, Ubuntu 26.04 LTS, Intel Xeon Platinum 8488C (Sapphire Rapids),
4 vCPU, 15 GiB RAM, 57 GB disk, no GPU
Engine: SGLang `lmsysorg/sglang@sha256:96fda92d0a108b72435de5e42ab5a3040abf17b6f61843de3bc51be50af996e8`
(tag `v0.5.13-xeon`), `--device cpu`
Model: Qwen/Qwen3.5-2B (first pass on 0.8B — see findings)

**Outcome: the deployment procedure in the DGX Spark guide completed end to end.**
Two defects in the guide were found and fixed; both would have surfaced on the
client's hardware.

---

## Acceptance results

| # | Check | Result |
|---|---|---|
| 1 | Model server starts from a pinned image and answers | PASS (~8 s startup) |
| 2 | Request without API key rejected | PASS — 401 |
| 3 | Engine port unreachable after isolation applied | PASS — connection refused |
| 4 | Gateway accepts `Authorization: Bearer` | PASS — 200 |
| 5 | Gateway accepts `x-api-key` (Anthropic style) | PASS — 200 |
| 6 | Nothing outside `/v1` exposed | PASS — `/metrics` returns 404 |
| 7 | Audit log records user/time/status, no prompt text | PASS |
| 8 | Model container has no route to the internet | PASS |
| 9 | Correctness check, 40 concurrent-batched requests | PASS — 0 wrong out of 40 |

Sample audit line (no request content present):

    {"time":"2026-09-24T10:38:38+00:00","user":"cwc-ai-service","ip":"172.20.0.1",
     "method":"GET","path":"/v1/models","status":"200","in_bytes":88,
     "out_bytes":239,"secs":0.004}

## Measurements (rehearsal hardware only — NOT indicative of the client's)

| | |
|---|---|
| Model load | 1.29 s |
| Server ready | ~8 s from container start |
| Weights in memory | 2.68 GB (0.8B) |
| KV cache | 3.48 GB, 304,849 tokens |
| Attention backend selected | `amx_attn` — AMX path active |
| Model download (0.8B) | ~20 s |
| SGLang image pull | ~2 min |

The guide's ~9-minute first boot does not occur here: that is torch compile and
CUDA graph capture, neither of which runs on the CPU backend.

---

## Defects found in the source guide

### 1. nginx will not start — `could not build map_hash`

**Severity: would block the client deployment.**

The gateway template maps per-user API keys with an nginx `map` block. Keys
generated the guide's own way (`openssl rand -hex 32`) are 64 characters, which
exceeds nginx's default 64-byte map hash bucket. nginx exits with:

    nginx: [emerg] could not build map_hash, you should increase map_hash_bucket_size: 64

The error names the bucket size but gives no hint that key length is the cause.

**Fix** — at the top of the gateway config:

    map_hash_bucket_size 128;
    map_hash_max_size 2048;

Applied to `templates/llm.conf.template` in this repo.

### 2. The correctness check cannot distinguish two different failures

`correctness-check.sh` compares against one fixed expected answer. A model too
small for the task fails every request identically, which looks like a total
failure but is not the concurrency fault the check exists to detect.

Observed: Qwen3.5-0.8B returned `1,3,7,19,25,42` on 40/40 requests — the same
wrong answer each time. A single isolated request returned the same value,
proving it was model capability, not scheduling. Qwen3.5-2B then passed 40/40.

**Rule for the client guide:** a correctness failure must always be followed by a
single-request comparison.
- Identical wrong answers, and wrong alone too → the model, not the engine.
- Correct alone but wrong under load → the concurrency fault the guide warns of.

Qwen3.5-0.8B is below the floor for this check. Start at 2B or above.

---

## Steps the guide assumes but does not state

- **Host preparation.** DGX OS ships with Docker and the NVIDIA Container
  Toolkit; a stock Ubuntu host does not. Installing `docker.io`,
  `docker-compose-v2` and `git`, adding the user to the `docker` group and
  reconnecting is a prerequisite step.
- **`docker compose version` must be checked early.** `!reset` and `!override`
  need 2.24.4+. Observed here: 2.40.3. Finding a shortfall at the gateway step
  wastes the whole download.
- **Shell scripts lose their executable bit** when the repo is cloned from a
  Windows-authored commit. `chmod +x *.sh` after cloning.
- **git does not track empty directories** — `tls/` must be created by hand
  before the certificate is generated.
- **`hf download`** is the current Hugging Face CLI form (not
  `huggingface-cli`). An `HF_TOKEN` is optional but gives better rate limits —
  worth having for a 24 GB model on a slow link.
- **Use `tmux`** for the download and first boot. The SSH session dropped three
  times during this rehearsal; without tmux each drop kills the running step.

---

## What this rehearsal did NOT validate

These carry no evidence from this run and must be marked untested in the client
guide:

- **NVFP4 weights** — Blackwell-only hardware format; not available on any
  non-Blackwell machine.
- **DFlash2 speculative decoding** — requires the Spark-specific ARM64 dev image.
  The `--speculative-*` flags were absent here.
- **`--mem-fraction-static 0.50`** — guards against a unified-memory freeze that
  cannot occur on a machine with no GPU. Must be respected on the Spark
  regardless; it remains untested.
- **Any timing figure.** CPU, 2B model. Nothing here predicts the Spark.
- **Vision throughput on fax page images.**
- **The ~9 minute first boot.**
