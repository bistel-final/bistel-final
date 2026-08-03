from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.router import router as agent_router
from app.analytics.router import router as analytics_router
from app.common.config import CORS_ORIGINS
from app.detection.router import router as detection_router
from app.knowledge.router import router as knowledge_router

app = FastAPI(
    title="BISTel FDC Agent API",
    description="LangGraph 기반 반도체 FDC 이상감지 에이전트 Backend",
    version="0.1.0",
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


app.include_router(detection_router)
app.include_router(knowledge_router)
app.include_router(agent_router)
app.include_router(analytics_router)
