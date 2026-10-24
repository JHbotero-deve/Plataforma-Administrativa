import asyncio
import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from api import _sync_store_catalog


def _error_detail(exc: Exception):
    """HTTPException convierte str(exc) en vacío; conservar el diagnóstico completo."""
    detail = getattr(exc, "detail", None)
    if detail is not None:
        return detail
    return {"type": type(exc).__name__, "message": str(exc) or repr(exc)}


async def sync_catalog_loop():
    enabled = os.getenv("CATALOG_AUTO_SYNC_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        print({"event": "catalog_auto_sync_disabled", "trigger": "Telegram /inyectar"}, flush=True)
        await asyncio.Event().wait()
        return
    interval = max(300, int(os.getenv("CATALOG_SYNC_INTERVAL_SECONDS", "7200")))
    print({"event": "catalog_worker_started", "interval_seconds": interval}, flush=True)
    while True:
        try:
            result = await asyncio.to_thread(_sync_store_catalog)
            print({"event": "catalog_sync", "result": result}, flush=True)
        except Exception as exc:
            print({"event": "catalog_sync_failed", "error": _error_detail(exc)}, flush=True)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_: FastAPI):
    sync_task = asyncio.create_task(sync_catalog_loop())
    try:
        yield
    finally:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Central Catalog Worker",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "central-worker"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))