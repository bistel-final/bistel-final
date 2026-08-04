from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.agent.router import router as agent_router
from app.analytics.router import router as analytics_router
from app.common.config import CORS_ORIGINS
from app.common.db import dispose_engines, engine, readonly_engine
from app.common.neo4j import close_neo4j_driver, get_neo4j_driver
from app.detection.router import router as detection_router
from app.knowledge.router import router as knowledge_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield

    dispose_engines()
    close_neo4j_driver()


app = FastAPI(
    title="BISTel FDC Agent API",
    description="LangGraph 기반 반도체 FDC 이상감지 에이전트 Backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["System"])
def readiness() -> dict[str, object]:
    services: dict[str, str] = {}

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        services["postgres"] = "available"
    except Exception:
        services["postgres"] = "unavailable"

    try:
        with readonly_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        services["postgres_readonly"] = "available"
    except Exception:
        services["postgres_readonly"] = "unavailable"

    try:
        get_neo4j_driver().verify_connectivity()
        services["neo4j"] = "available"
    except Exception:
        services["neo4j"] = "unavailable"

    if "unavailable" in services.values():
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "services": services},
        )

    return {"status": "ready", "services": services}


app.include_router(detection_router)
app.include_router(knowledge_router)
app.include_router(agent_router)
app.include_router(analytics_router)
