# BISTel FDC Agent

LangGraph 기반 반도체 FDC 이상감지 에이전트의 FastAPI·React 모노레포입니다.

> [!CAUTION]
> **신규 `kosa_0813.zip` 기준 v2 전환 중입니다.** 구 v1.9/v1.10/v9.6 문서, 51건 알람,
> Fault 정답, ACT-0001~0010, 4단계 조치와 기존 API/PDF 산출물은 구현 기준으로 사용하지 않습니다.
> 현재 기준은 v2 요구사항·설계·역할분담 v10과 WBS v4입니다.

PHOTO·ETCH 2개 AREA의 parameter 데이터(FDC trace)를 WAFER 단위로 요약해 규칙과 비지도
이상 점수로 감지(Detection)하고, 발생한 알람에 대해 LangGraph 에이전트가 장비 관계(Neo4j)와
장비 문서(RAG)를 근거로 원인 가설과 조치를 제시합니다. `EQP_HOLD`는 승인요청 이메일을 먼저
보내고 사람 승인(HITL) 후에만 MES Mock을 호출하며 전 과정을 감사로그로 기록합니다.

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
infra/      source/bootstrap · nginx · n8n workflow
docs/       사양 원본과 AI 작업 문서
```

일부 하위 산출물은 해당 V4 Task에서 추가합니다. 전체 목표 구조는
[시스템 설계서 v2.0 작업본 2.1](docs/specifications/시스템설계서_v2_0_작업본.md)을 따릅니다.

## 문서

**v2 전환 기준** — 기능 동작·수용 기준과 현재 작업 범위의 근거

- [요구사항 정의서 v2.0 작업본](docs/specifications/요구사항정의서_v2_0_작업본.md)
- [시스템 설계서 v2.0 작업본](docs/specifications/시스템설계서_v2_0_작업본.md)
- [역할분담 v10.0 작업본](docs/specifications/FDC_프로젝트_역할분담_v10_0_작업본.md)
- [Task 분해 WBS v4 작업본](docs/planning/Task분해_WBS_v4_작업본.md)
- [API·Tool v2 영향표](docs/planning/V4-CM-0.3_API_Tool_영향표.md)

**작업 문서**

- [AI 작업 문서](docs/ai-context/README.md) — 라우팅 표 · 문서 우선순위
- [개발 규칙](docs/development-guide.md) — Git · PR · 테스트 실행

`docs/ai-context/01`~`07`, `PROMPT_TEMPLATE.md`, `tasks/*.md`는 v1.9/v1.10/v9.6 구 이력으로
사용 중지 상태입니다. v2 재생성 전에는 AI 작업 문서의 라우팅을 따라 원본과 해당 V4 Task를 직접 읽습니다.

**구 API 산출물 — 전환 중 / 사용 중지**

- [API 명세서 Markdown](docs/deliverables/api/API명세서.md)
- [API 명세서 CSV](docs/deliverables/api/API명세서.csv)
- [API 명세서 PDF](docs/deliverables/api/API명세서.pdf)

위 v2.1 3종은 신규 v2 baseline 이전 산출물이므로 현재 계약 기준이 아닙니다. 재생성 전 API 계약은
시스템 설계서 v2.0 작업본 8~9장과 `V4-CM-0.3_API_Tool_영향표.md`를 따릅니다.

**구 제출용 PDF — v2 재생성 전 사용 중지**

- [시스템 설계서 PDF](docs/deliverables/system-design/시스템설계서.pdf) — `build.py`가 그림 11종을 얹어 생성
- [요구사항 정의서 PDF](docs/deliverables/requirements-spec/요구사항정의서.pdf) — `build.py`가 그림·실제 화면 캡처 10종을 얹어 생성
- 두 PDF는 구 baseline 제출본입니다. v2 원본 확정 뒤 재생성하며, 그 전에는 구현 근거로 사용하지 않습니다.

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
두 health 경로는 내부 운영·개발 진단용이며 업무 API 목록에는 포함하지 않습니다. 차기 API 명세를
재생성할 때도 별도 운영 endpoint로 둡니다.

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

> **Agent E2E는 공용 서버의 전용 논리 DB `kosa_agent_e2e`에서만 실행합니다.** reset guard는
> host·DB·token을 확인하고 `kosa_agent`·`kosa_text2sql` reset을 거부해야 합니다.
>
> 제외는 **마커 기반**입니다. 모든 E2E 테스트에 `@pytest.mark.e2e`를 반드시 지정합니다.
> V4-CM-3.4·V4-C-8.2의 reset guard가 구현·검증되기 전에는 `pytest -m e2e`를 실행하지 않습니다.
> 절차는 [개발 규칙 4장](docs/development-guide.md)을 따릅니다.

## 배포 패키지

신규 ZIP·원본 Generator는 불변 입력으로 보존하고 직접 수정하지 않습니다. V4-CM-1에서 별도
corrected generator·corrected copy·manifest를 만들고 검증한 뒤 profile별 공용 DB에 적재합니다.
원본과 corrected layer, Runtime, evaluation artifact를 서로 같은 기준값으로 취급하지 않습니다.

상세 전환 순서와 안전 조건은 WBS `V4-CM-1.*`·`V4-CM-2.*`와
[bootstrap README](infra/bootstrap/README.md)를 따릅니다. 구 2-profile 검증 명령은 v4 manifest 전환 전에는
신규 데이터 완료 증빙으로 사용하지 않습니다.
