from fastapi import FastAPI

app = FastAPI(title="Glaka")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check: returns 200 if the process is up."""
    return {"status": "ok"}
