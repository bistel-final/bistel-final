# BISTel FDC Agent

LangGraph 기반 반도체 FDC 이상감지 에이전트의 FastAPI·React 모노레포입니다.

PHOTO·ETCH 2개 AREA의 센서 데이터(FDC trace)를 WAFER 단위로 요약해 규칙과 이상감지 모델로 감지(Detection)하고, 발생한 알람에 대해 LangGraph 에이전트가 장비 관계(Neo4j)와 장비 매뉴얼(RAG)을 근거로 이상 유형을 분류·원인 분석·조치 권고(Classification)합니다. EQUIPMENT HOLD는 사람 승인(HITL) 후에만 전송하며 전 과정을 감사로그로 기록합니다.

## 기술 스택

- Python 3.12 / FastAPI
- React 19 / Vite
- PostgreSQL 16 + pgvector
- Neo4j 5 Community
- LangGraph 0.2.53
- n8n
- pytest / Ruff

## 최종 목표 구조

```text
backend/    FastAPI · AI · Tool · 마이그레이션 · 운영 스크립트
frontend/   React 데이터 플랫폼 (8화면)
infra/      bootstrap · nginx · n8n workflow          (미생성)
docs/       사양 원본과 AI 작업 문서
```

`infra/`를 비롯한 일부 디렉터리는 해당 산출물이 생기는 시점에 만듭니다. 전체 목표 구조는 [시스템 설계서 2.1](docs/specifications/시스템설계서_v1_10_최종.md)을 따릅니다.

## 문서

**원본 사양** — 기능 동작·수용 기준의 최종 근거

- [요구사항 정의서 v1.9](docs/specifications/요구사항정의서_v1_9_최종.md)
- [시스템 설계서 v1.10](docs/specifications/시스템설계서_v1_10_최종.md)
- [역할분담 v9.6](docs/specifications/FDC_프로젝트_역할분담_v9.6\(최종\).md)

**작업 문서**

- [AI 작업 문서](docs/ai-context/README.md) — 라우팅 표 · 문서 우선순위
- [강제 규칙](docs/ai-context/01-project-rules.md) — 계약·보안·계층·예산
- [도메인 규칙과 불변 수치](docs/ai-context/02-domain-rules.md)
- [개발 규칙](docs/development-guide.md) — Git · PR · 테스트 실행

**API 산출물**

- [API 명세서 Markdown](docs/deliverables/api/API명세서.md) — 검색·리뷰용
- [API 명세서 CSV](docs/deliverables/api/API명세서.csv) — 표 편집·검토용
- [API 명세서 PDF](docs/deliverables/api/API명세서.pdf) — 제출·회람용
- 세 형식은 `docs/deliverables/api/build_api_spec.py`에서 함께 생성합니다.

**제출용 PDF** — 원본 사양 md에 아키텍처·화면 그림을 얹어 만든 최종 제출본

- [시스템 설계서 PDF](docs/deliverables/system-design/시스템설계서.pdf) — `build.py`가 그림 11종을 얹어 생성
- [요구사항 정의서 PDF](docs/deliverables/requirements-spec/요구사항정의서.pdf) — `build.py`가 그림·실제 화면 캡처 10종을 얹어 생성
- 두 스크립트 모두 원본 md는 고치지 않습니다. 그림은 렌더링 단계에서만 끼워 넣습니다(`docs/deliverables/_shared/doc_pdf.py`).

AI 코딩 도구는 `CLAUDE.md`(Claude Code)와 `AGENTS.md`(Codex)를 통해 위 문서로 진입합니다.

## Backend 실행

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

환경변수는 **저장소 루트 `.env`** 에서 관리합니다. PostgreSQL·Neo4j·n8n 주소와 LLM 설정을 팀이 사용하는 실제 서버 값으로 채웁니다. `.env`는 Git에 커밋하지 않습니다.

## 확인 경로

| 경로 | 의미 |
|---|---|
| `http://localhost:8000/docs` | OpenAPI 문서 |
| `http://localhost:8000/health` | API 프로세스 생존. 외부 장애와 무관하게 200 |
| `http://localhost:8000/health/ready` | PostgreSQL·Neo4j·n8n readiness. 하나라도 실패하면 의존성별 상태와 함께 503 |

`/health/ready`가 503이어도 API 프로세스는 종료되지 않습니다.

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

`pytest`는 `backend/pytest.ini` 설정에 따라 **`e2e` 마커가 지정된 테스트를 제외하고 실행**합니다.

> **E2E는 격리 DB에서만 실행합니다.** `pytest -m e2e`는 `action_history`·`agent_run`·`approval_request`·`audit_log` 등 실행 데이터를 비운 상태를 전제하므로, 공용 개발 서버에서 실행하면 배포 정답 데이터(ACT-0001~0010)가 손상됩니다.
>
> 제외는 **마커 기반**입니다. 모든 E2E 테스트에 `@pytest.mark.e2e`를 반드시 지정합니다. 경로 자동 마킹과 공용 호스트 거부 검사가 구현되기 전까지는 **공용 서버에 연결된 상태에서 E2E를 실행하지 않습니다.** 절차는 [개발 규칙 4장](docs/development-guide.md)을 따릅니다.

## 배포 패키지

기준정보·생산 데이터·문서 임베딩·Neo4j 관계는 멘토 배포패키지로 적재가 완료된 상태입니다. `01_schema.sql`·원본 CSV·`master.cypher`는 **수정하지 않습니다.** 추가 테이블·컬럼·인덱스는 `backend/migrations/`의 별도 마이그레이션으로만 관리합니다.
