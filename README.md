# BISTel FDC Agent Backend

LangGraph 기반 반도체 FDC 이상감지 에이전트의 FastAPI Backend입니다.

## 기술 스택

- Python 3.12
- FastAPI
- PostgreSQL / pgvector
- Neo4j
- LangGraph
- n8n
- Ollama
- pytest / Ruff

## 로컬 실행

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`.env`의 PostgreSQL·Neo4j·n8n 주소는 팀이 사용하는 실제 서버로 설정합니다. `.env`는 Git에 커밋하지 않습니다.

## 확인 경로

- Swagger UI: `http://localhost:8000/docs`
- Liveness: `http://localhost:8000/health`
- DB readiness: `http://localhost:8000/health/ready`

## 코드 품질

```bash
ruff format .
ruff check .
pytest
```

## 문서

- [아키텍처](docs/architecture.md)
- [API·Tool 계약](docs/contracts.md)
- [개발 규칙](docs/development-guide.md)
