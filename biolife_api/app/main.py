from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.dependencies import build_llm_client
from app.routers import ad_generator, campaign

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.llm_client = build_llm_client(settings)
    yield
    close = getattr(app.state.llm_client, "aclose", None)
    if close:
        await close()


app = FastAPI(title="BioLife AI Reel Generator", version="3.0.0", lifespan=lifespan)
app.include_router(ad_generator.router)
app.include_router(campaign.router)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    s = get_settings()
    return {"status": "ok", "llm_provider": s.llm_provider}
