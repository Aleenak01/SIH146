"""`python -m backend` starts the API on the configured local address (default 127.0.0.1:8000)."""

import uvicorn

from .config import load_settings

if __name__ == "__main__":
    s = load_settings()
    uvicorn.run("backend.main:app", host=s.host, port=s.port, log_level="info")
