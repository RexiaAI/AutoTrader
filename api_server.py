import os
import sys
import uvicorn
import logging
import fcntl
from pathlib import Path
from dotenv import load_dotenv

# Configure logging to write to both stderr and a file immediately.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler("api_server.log", mode="a")
    ]
)
logger = logging.getLogger("api_server")

def _load_local_env() -> None:
    """
    Load local environment variables from config/secrets.env (if present).

    This keeps `python api_server.py` consistent with `python main.py` for local development.
    """
    env_path = Path(__file__).resolve().parent / "config" / "secrets.env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info("Loaded environment variables from %s", env_path)

def main() -> None:
    _load_local_env()

    # Enforce single-instance operation.
    # If you try to start a second API while one is already running, we fail fast with a clear message.
    lock_path = Path(".api_server.lock")
    try:
        lock_f = lock_path.open("w")
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_f.write(str(os.getpid()))
        lock_f.flush()
        # Keep lock file handle alive for process lifetime.
    except Exception:
        logger.error("Another API instance appears to be running (lockfile busy). Exiting.")
        sys.exit(1)

    try:
        # For local development:
        # - React dev server proxies /api → this backend.
        # - The trader (`main.py`) writes into the configured database (PostgreSQL recommended).
        
        logger.info("Starting AutoTrader API server on 127.0.0.1:8000")
            
        uvicorn.run(
            "src.api.app:app", 
            host="127.0.0.1", 
            port=8000, 
            reload=False,
            log_level="info",
            loop="auto",
            workers=1  # Strictly 1 worker to minimize SQLite locking issues
        )
    except Exception as e:
        logger.error(f"Fatal error in API server: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


