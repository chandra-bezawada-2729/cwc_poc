# Generates a random API key for the local Qwen server and writes it to
# local-llm\.env. Paste the same key into cwc-ai-service\.env as OPENAI_API_KEY.
# Re-running replaces the key: restart the container and update the AI service.
$ErrorActionPreference = "Stop"
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$key = "cwc-local-" + (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")

$envFile = Join-Path $PSScriptRoot ".env"
@(
  "LLM_API_KEY=$key"
  "LLM_MODEL=unsloth/Qwen3.5-9B-GGUF:Q4_K_M"
  "LLM_ALIAS=qwen3.5-9b"
  "LLM_CONTEXT=32768"
  "LLM_GPU_LAYERS=99"
) | Set-Content -Path $envFile -Encoding ascii

Write-Host "Wrote $envFile"
Write-Host ""
Write-Host "Put these lines in cwc-ai-service\.env:"
Write-Host "  LLM_PROVIDER=openai"
Write-Host "  OPENAI_BASE_URL=http://localhost:8081/v1"
Write-Host "  OPENAI_API_KEY=$key"
Write-Host "  OPENAI_MODEL=qwen3.5-9b"
