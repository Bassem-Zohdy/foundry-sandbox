"""A tiny API used to try Foundry's checks on GitHub."""
from fastapi import FastAPI

app = FastAPI(title="foundry-sandbox")


@app.get("/healthz")
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}
