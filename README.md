# BISTel FDC Agent

LangGraph 기반 반도체 FDC 이상감지 에이전트의 FastAPI·React 모노레포입니다.

> [!CAUTION]
> **FINAL-DOC — 멘토님 제공 최종 `project.zip` 기준 문서의 교차검토를 완료했습니다.** `kosa_0813`,
> 요구사항·설계 v2.0 이하, 역할분담 v10.0 이하, WBS v4 이하와 기존 API/PDF는 이전 epoch
> 이력으로만 보존하며 신규 구현 근거로 사용하지 않습니다. v2.1·v10.1·API v3·WBS v5와
> 역할별 Task의 교차검토가 완료됐습니다. 구현은 리뷰된 `V5-*` Task와 선행 게이트를 따르며,
> 충돌이 발견되면 구현보다 상위 문서 정합화를 먼저 합니다.

Photo·Etch 2개 AREA의 parameter 데이터(FDC trace)를 WAFER 단위로 요약해 규칙으로 알람을
재현하고, 비지도 anomaly score는 설명 보조 근거로 제공합니다. LangGraph 에이전트는
PostgreSQL·Neo4j·RAG 근거로 원인 가설을 만들며 조치는 규칙으로만 결정합니다. `EQP_HOLD`는
승인 요청 이메일을 먼저 보내고 사람 승인(HITL) 후에만 Kafka MES Mock 이벤트를 발행합니다.

## 기술 스택

- Python 3.12 / FastAPI
- React 19 / Vite
- PostgreSQL 16 + pgvector (고정 문서·chunk·1024차원 embedding)
- Neo4j 5 Community
- LangGraph 0.2.53
- Apache Kafka (`fdc.actions` / `fdc.actions.result` MES Mock)
- n8n (SMTP·Kafka workflow)
- pytest / Ruff

## 최종 목표 구조

```text
backend/    FastAPI · AI · Tool · 마이그레이션 · 운영 스크립트
frontend/   React 데이터 플랫폼 (최종 5화면, 상세·확장 route 별도)
infra/      source/bootstrap · nginx · n8n workflow
docs/       사양 원본과 AI 작업 문서
```

하위 산출물의 신규·유지·폐기 범위는 교차검토가 끝난 WBS v5 작업본을 따릅니다. 전체 목표 구조는
[시스템 설계서 v2.1 작업본](docs/specifications/시스템설계서_v2_1_작업본.md)을 따릅니다.

## 문서

**최종 데이터 전환 기준** — 기능 동작·수용 기준과 현재 작업 범위의 근거

- [최종 패키지 검증 기준표](docs/reference/mentor-final-20260818/README.md)
- [요구사항 정의서 v2.1 작업본](docs/specifications/요구사항정의서_v2_1_작업본.md)
- [시스템 설계서 v2.1 작업본](docs/specifications/시스템설계서_v2_1_작업본.md)
- [역할분담 v10.1 작업본](docs/specifications/FDC_프로젝트_역할분담_v10_1_작업본.md)
- [API 명세서 v3 작업본](docs/deliverables/api/API명세서_v3_작업본.md)
- [WBS v5 작업본](docs/planning/Task분해_WBS_v5_작업본.md)
- 역할별 Task 작업본: [A](docs/ai-context/tasks/A-detection.md) · [B](docs/ai-context/tasks/B-knowledge.md) · [C](docs/ai-context/tasks/C-agent.md) · [D](docs/ai-context/tasks/D-analytics.md)

위 문서는 모두 최종 패키지 전환 작업본이며 교차검토를 완료했습니다. 문서 검토 완료가 각
`V5-*` 구현·공용 적용 완료를 뜻하지 않으므로 실제 상태는 WBS와 Task의 완료 기준으로 판단합니다.

**작업 문서**

- [AI 작업 문서](docs/ai-context/README.md) — 라우팅 표 · 문서 우선순위
- [개발 규칙](docs/development-guide.md) — Git · PR · 테스트 실행

`docs/ai-context/01`~`07`과 `PROMPT_TEMPLATE.md`는 이전 epoch 이력으로 사용 중지 상태입니다.
`docs/ai-context/tasks/*.md`는 WBS v5와 정합화한 현행 역할별 작업본입니다. AI 작업 문서의
라우팅을 따라 새 기준표와 원본 작업본을 직접 읽습니다.

**구 API 산출물 — 전환 중 / 사용 중지**

- [API 명세서 Markdown](docs/deliverables/api/API명세서.md)
- [API 명세서 CSV](docs/deliverables/api/API명세서.csv)
- [API 명세서 PDF](docs/deliverables/api/API명세서.pdf)

위 기존 3종은 최종 패키지 이전 산출물이므로 현재 계약 기준이 아닙니다. 새 계약은 교차검토를
마친 API 명세서 v3 작업본을 따르며 PDF는 대응 DTO·생성기 Task 완료 뒤 재생성합니다.

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

환경변수는 **저장소 루트 `.env`** 에서 관리합니다. PostgreSQL·Neo4j·n8n·Kafka 주소와
topic, LLM 설정을 팀이 사용하는 실제 서버 값으로 채웁니다. reference migration marker와
Neo4j·RAG 적재 상태는 환경변수로 임의 지정하는 revision이 아니라 실제 적재 결과로 검증하는
readiness 상태입니다. `.env`는 Git에 커밋하지 않습니다.

공용 PostgreSQL·Neo4j·n8n은 외부 canonical 서비스로 연결합니다. 팀 compose 범위는
Backend·Frontend·Kafka·MES Mock뿐이며, 공용 서비스와 경쟁하는 두 번째 DB·Neo4j·n8n을
기동하지 않습니다.

## 확인 경로

| 경로 | 의미 |
|---|---|
| `http://localhost:8000/docs` | OpenAPI 문서 |
| `http://localhost:8000/health` | API 프로세스 생존. 외부 장애와 무관하게 200 |
| `http://localhost:8000/health/ready` | PostgreSQL epoch·schema·role, reference migration marker, Neo4j 44/85 marker, RAG 필수 문서 3종·vector non-null·1024차원·검색 smoke, n8n, Kafka metadata·필수 topic 준비 상태. 하나라도 실패하면 의존성별 상태와 함께 503 |

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
> WBS v5의 E2E reset guard가 구현·검증되기 전에는 `pytest -m e2e`를 실행하지 않습니다.
> 절차는 [개발 규칙 4장](docs/development-guide.md)을 따릅니다.

## 배포 패키지

최종 ZIP·원본 Generator는 불변 입력으로 보존하고 직접 수정하지 않습니다. WBS v5의 intake
Task에서 선별 artifact·manifest를 등록하고 격리 DB 검증 뒤 profile별 공용 DB에 적용합니다.
원본과 correction layer, Runtime, evaluation artifact를 서로 같은 기준값으로 취급하지 않습니다.

기존 WBS v4와 `kosa_0813` bootstrap은 이전 이력입니다. 최종 전환 순서는 교차검토를 마친
WBS v5를 따르며, 기존 manifest 검증 결과를 최종 데이터 완료 증빙으로 사용하지 않습니다.

최종 canonical 화면은 Dashboard·Alarm History·Agent·Documents·Ontology입니다.
Text2SQL·Analytics는 선택 확장으로 분리하며, Agent 자연어 질의는 `POST /agent/ask` 계약을
사용합니다. Ontology 화면의 public 계약은 선택 chamber의 subgraph와 context를 함께 반환하는
`GET /relations/chambers/{chamber_id}` 하나입니다. RAG는 corpus revision·`ACTIVE` 전환·overlay를
운영하지 않고, 검증된 원본 3문서의 corrected artifact와 고정 chunk/vector 계약을 사용합니다.
필수 public 업무 API는 **11개**(외부 최소 호환 9개 + 보안 필수 chamber API 1개 + 실행 API
`POST /agent/runs` 1개)입니다. 이와 별도로 internal delivery callback 1개와 업무 API 수에서
제외하는 운영 `/health`·`/health/ready` 2개를 둡니다.
