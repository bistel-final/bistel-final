# BISTel FDC Agent

LangGraph 기반 반도체 FDC 이상감지 에이전트의 FastAPI·React 모노레포입니다.

## 기술 스택

- Python 3.12
- FastAPI
- React 19 / Vite
- PostgreSQL / pgvector
- Neo4j
- LangGraph
- n8n
- pytest / Ruff

## 저장소 구조

```text
backend/    FastAPI와 AI·Tool
frontend/   React 데이터 플랫폼
docs/       공통 문서
```

## Backend 실행

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`.env`의 PostgreSQL·Neo4j·n8n 주소는 팀이 사용하는 실제 서버로 설정합니다. `.env`는 Git에 커밋하지 않습니다.

## 확인 경로

- Swagger UI: `http://localhost:8000/docs`
- Liveness: `http://localhost:8000/health`
- DB readiness: `http://localhost:8000/health/ready`

## Frontend 실행

Node 버전은 `.nvmrc`의 `22.14.0`을 사용합니다.

```bash
cd frontend
cp .env.example .env
npm ci
npm run dev
```

- React: `http://localhost:5173`
- API 주소: `frontend/.env`의 `VITE_API_BASE_URL`

## 코드 품질

```bash
cd backend
ruff format .
ruff check .
pytest

cd ../frontend
npm run lint
npm run build
```

## 문서

- [아키텍처](docs/architecture.md)
- [API·Tool 계약](docs/contracts.md)
- [개발 규칙](docs/development-guide.md)
