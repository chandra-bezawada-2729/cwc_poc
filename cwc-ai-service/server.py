import os
import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the directory containing this file, overriding any stale user-level env vars.
_ENV_PATH = Path(__file__).parent.resolve() / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=True)

from flask import Flask
from controllers.health_controller import health_bp
from controllers.classify_controller import classify_bp
from controllers.extraction_controller import extraction_bp

# ── Logging ──────────────────────────────────────────────────────────────────
log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / "cwc-ai-service.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  [%(asctime)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Startup credential sanity check — never logs the key itself.
_raw_key = os.getenv("ANTHROPIC_API_KEY", "")
_key = _raw_key.strip().strip('"').strip("'")
if _key:
    logger.info(
        f"[STARTUP] ANTHROPIC_API_KEY loaded: length={len(_key)}, last4={_key[-4:]!r}, "
        f"prefix_ok={_key.startswith('sk-ant-')}"
    )
else:
    logger.warning("[STARTUP] ANTHROPIC_API_KEY is empty — classification will fail")

# ── App factory ───────────────────────────────────────────────────────────────
app = Flask(__name__)

app.register_blueprint(health_bp)
app.register_blueprint(classify_bp)
app.register_blueprint(extraction_bp)

if __name__ == "__main__":
    port = int(os.getenv("SERVER_PORT", 5002))
    host = os.getenv("SERVER_HOST", "127.0.0.1")     # localhost only; the backend is the only caller
    # CWC_SERVER=waitress (production) or flask (development, the default on a laptop).
    server = os.getenv("CWC_SERVER", "flask").strip().lower()
    logger.info(f"[STARTUP] CWC AI service starting on {host}:{port} ({server})")
    if server == "waitress":
        from waitress import serve
        # Each request mostly waits on the model server, so threads are cheap.
        # channel_timeout covers a slow multi-page fax on the local model.
        serve(app, host=host, port=port,
              threads=int(os.getenv("CWC_SERVER_THREADS", "8")),
              channel_timeout=int(os.getenv("CWC_LLM_TIMEOUT_SECS", "600")) + 60)
    else:
        app.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
