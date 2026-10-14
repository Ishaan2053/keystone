from fastapi import FastAPI

app = FastAPI(title="Keystone API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
