import uvicorn
from core.logging import setup_logging

setup_logging()

from api.routes import app

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8082, reload=True)