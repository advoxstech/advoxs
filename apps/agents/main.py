import uvicorn

from core.logging import setup_logging

setup_logging()

# Reexportado pro uvicorn conseguir carregar "main:app" (chamado abaixo e pelo
# entrypoint do container) — a referência real é só pela string, então o ruff
# marca como não usado sem o noqa.
from api.routes import app  # noqa: E402,F401

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8082, reload=True)
