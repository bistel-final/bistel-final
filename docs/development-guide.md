# Development Guide

## 1. 전체 작업 흐름

```text
필요한 경우 Issue 생성
→ main 최신화
→ 작업 브랜치 생성
→ 기능 개발 및 로컬 검증
→ Commit
→ Push
→ Pull Request
→ PR Policy 통과
→ 팀원 Review
→ Squash and merge
→ 로컬·원격 브랜치 정리
```

초기 공통 세팅 이후에는 `main`에 직접 commit 또는 push하지 않습니다.

## 2. Issue 규칙

기능·버그·협업 작업을 추적할 필요가 있는 경우 GitHub Issue를 생성합니다. 간단한 문서·설정 수정은 Issue 없이 진행해도 됩니다.

### Issue 템플릿

- `기능 개발`: 새로운 기능, API, Tool, React 연동
- `버그 신고`: 기능 오류, 연결 오류, 통합 오류
- `공통 작업`: 문서, 테스트, 리팩터링, Docker, GitHub 설정

Issue에는 다음 내용을 작성합니다.

- 담당 영역: Common / A / B / C / D
- 작업 목적과 이유
- 세부 작업 체크리스트
- 완료 기준
- 선행 작업 또는 관련 Issue

기능 하나를 하나의 Issue로 관리하고, 작업 범위가 크면 여러 Issue로 분리합니다.

## 3. Branch 규칙

### 형식

```text
<type>/<area>-<description>
```

브랜치명은 영문 소문자, 숫자, 하이픈만 사용합니다.

### Type

| Type | 용도 |
|---|---|
| `feat` | 새로운 기능, API, Tool |
| `fix` | 버그 수정 |
| `refactor` | 동작 변경 없는 코드 구조 개선 |
| `test` | 테스트 추가·수정 |
| `docs` | 문서 추가·수정 |
| `chore` | 환경, GitHub, 패키지, Docker 설정 |

### Area

| Area | 담당 영역 |
|---|---|
| `common` | 공통 설정·DB 연결·GitHub |
| `detection` | A - FDC Detection |
| `knowledge` | B - Neo4j·RAG |
| `agent` | C - LangGraph·HITL·n8n |
| `analytics` | D - Text2SQL·통계·차트 |
| `integration` | 공통 통합·Docker·React 연동 |

### 예시

```text
feat/detection-summary
feat/knowledge-document-search
feat/agent-hitl
feat/analytics-text2sql
fix/common-db-connection
fix/agent-duplicate-send
docs/common-api-contracts
chore/integration-docker-compose
```

### 브랜치 생성

```bash
git switch main
git pull origin main
git switch -c feat/detection-summary
```

## 4. 개발 및 로컬 검증

각 담당자는 Backend 구현뿐 아니라 실제 서버와 React 연동까지 직접 확인합니다.

Backend 변경 시 커밋 전에 실행합니다.

```bash
cd backend
ruff format .
ruff check .
pytest
```

`pytest`는 `backend/pytest.ini`의 `addopts = -m "not e2e"`에 따라 **`e2e` 마커가 지정된 테스트를 제외하고 실행**합니다.

> 제외는 **마커 기반**입니다. `tests/e2e/`에 두더라도 `@pytest.mark.e2e`를 빼먹으면 일반 `pytest`에서 실행됩니다. 모든 E2E 테스트에 마커를 반드시 지정합니다. 경로 자동 마킹(`conftest.py`)과 공용 호스트 거부 검사는 추후 구현합니다.

### E2E 실행 (파괴적 — 격리 DB에서만)

E2E는 `action_history`·`agent_run`·`agent_run_alarm`·`agent_tool_call`·`approval_request`·`audit_log`·`action_delivery`·운영 `nl_query_log`와 Checkpoint 실행 데이터를 **비운 상태**를 전제합니다. 공용 개발 서버에서 실행하면 멘토 배포 정답 데이터(ACT-0001~0010)가 손상됩니다.

**격리 DB 검증 장치가 구현되기 전까지는 공용 서버에 연결된 `.env` 상태에서 E2E를 실행하지 않습니다.** 대상 DB를 직접 확인한 뒤 수동으로 실행합니다.

```bash
# 1. 접속 대상이 격리 DB인지 눈으로 확인한다. 공용 서버면 여기서 중단한다.
#    (backend/scripts/reset_agent_e2e_db.py 는 아직 미구현이다.
#     DB명 검사만으로는 공용 DB를 구분할 수 없으므로 호스트까지 함께 확인한다.)

# 2. E2E만 실행한다.
cd backend
pytest -m e2e
```

지켜야 할 조건입니다.

- 대상은 **격리 Compose DB 또는 전용 테스트 DB**입니다. 공용 교육장 서버를 대상으로 실행하지 않습니다.
- 공용 PostgreSQL·Neo4j·n8n 컨테이너를 `docker stop`으로 멈추지 않습니다. 장애 주입은 dependency override·Tool mock·테스트 webhook으로 합니다.
- `ACT-0001~0010`은 DB에 적재하지 않고 `backend/tests/fixtures/expected_actions.json`으로 비교합니다.
- 기준 데이터(`fdc_alarm` 51건 등 입력 6종, 기준정보, 문서 3·청크 39)는 보존합니다.

근거: 요구사항 13장 「테스트 데이터 격리 원칙」 · 시스템설계서 14.1~14.2

### 테스트 계층

| 계층 | 대상 | 마커 |
|---|---|---|
| Unit | 요약, R03, feature, `decide_action`, sqlglot, chart 규칙 | 없음 |
| Contract | Tool 5종 정상·오류·timeout JSON | `contract` |
| Integration | PostgreSQL Repository, Neo4j, pgvector, checkpoint, 승인 트랜잭션 | `integration` |
| E2E | FastAPI + React + DB + n8n 골든 시나리오 | `e2e` |
| Evaluation | ML, Fault 분류, RAG, 관계, Text2SQL, Level 1·2 | `evaluation` |

필요한 경우 FastAPI를 실행해 실제 연동을 확인합니다.

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

확인 경로:

```text
http://localhost:8000/docs          OpenAPI 문서
http://localhost:8000/health        API 프로세스 생존 (외부 장애와 무관하게 200)
http://localhost:8000/health/ready  PostgreSQL·Neo4j·n8n 준비 상태 (하나라도 실패 시 503)
```

GitHub Actions에서는 Ruff·pytest·실제 서버 연결을 실행하지 않습니다. 실행 결과는 PR의 `확인 방법`과 체크리스트에 기록합니다.

Frontend 변경 시 Node `.nvmrc` 버전과 `package-lock.json`을 기준으로 설치하고 검증합니다.

```bash
cd frontend
cp .env.example .env
npm ci
npm run lint
npm run build
npm run dev
```

Frontend API 주소는 `frontend/.env`의 `VITE_API_BASE_URL`로 관리합니다. 각 담당자는 Loading·Error·Empty 상태와 실제 FastAPI 연결을 함께 확인합니다.

## 5. Commit 규칙

### 형식

```text
<type>: <한 줄 요약>
```

본문에는 무엇을 왜 변경했는지 작성합니다.

### 예시

```bash
git add backend/app/detection backend/tests/unit backend/tests/contract frontend/src/features/detection

git commit \
  -m "feat: implement FDC summary API" \
  -m "센서 요약 결과를 Agent와 React에서 사용할 수 있도록 조회 API와 Tool을 추가한다."
```

비밀번호, API Key, 실제 `.env`, 모델 파일(`*.joblib`), 임베딩 캐시(`backend/model-cache/`)를 커밋하지 않습니다. 반대로 `backend/artifacts/*.json` manifest는 재현성 근거이므로 **커밋합니다**.

테스트 파일은 계층별 폴더에 둡니다. 도메인별 폴더(`tests/detection` 등)를 만들지 않습니다.

목표 구조입니다. 현재는 `tests/test_health.py`만 있으며 각 담당자가 자기 파트를 구현하면서 만듭니다.

```text
backend/tests/
├── unit/  ├── contract/  ├── integration/  ├── e2e/  └── fixtures/
```

## 6. Push 규칙

최초 push:

```bash
git push -u origin feat/detection-summary
```

이후 같은 브랜치에서 추가 push:

```bash
git push
```

## 7. Pull Request 규칙

PR의 base는 `main`, compare는 작업 브랜치로 설정합니다.

### PR 제목

형식:

```text
[담당영역] 변경 요약
```

담당영역은 `Common`, `A`, `B`, `C`, `D` 중 하나를 사용합니다.

예시:

```text
[A] FDC 요약 조회 API 구현
[B] 문서 검색 모델 필터 오류 수정
[Common] API 계약 문서 보완
```

### PR 본문

필수 항목은 두 개입니다.

```text
## 변경 내용
## 변경 이유
```

관련 Issue와 확인 사항은 있는 경우만 작성합니다.

```text
Closes #12
```

`Closes #12`를 작성한 PR이 `main`에 병합되면 연결된 Issue가 자동으로 종료됩니다.

## 8. PR Policy 자동 검사

PR을 생성·수정·재오픈하거나 작업 브랜치에 추가 push하면 `PR Policy / Validate PR`이 실행됩니다.

자동 검사 항목:

- 브랜치명 형식
- PR 제목 `[담당영역] 변경 요약`
- `## 변경 내용`
- `## 변경 이유`

PR Policy가 실패하면 `Checks`의 오류 메시지를 확인하고 브랜치 또는 PR 본문을 수정합니다. PR 본문을 수정하면 자동으로 다시 실행됩니다.

## 9. Review 및 Merge 규칙

- 최소 1명의 다른 팀원이 변경 내용을 확인합니다.
- PR Policy가 성공한 뒤 병합합니다.
- API·Tool 계약을 변경한 경우 원본(`docs/specifications/시스템설계서_v1_2_최종.md` 10장·10.6)을 먼저 고치고 요약(`docs/ai-context/04-api-tool-contracts.md`)을 동기화했는지 확인합니다.
- 기능 담당자가 실제 PostgreSQL·Neo4j·n8n·React 연동 결과를 PR에 기록했는지 확인합니다.
- E2E를 실행한 경우 **격리 DB에서 수행했음**을 PR에 명시했는지 확인합니다.
- 병합 방식은 `Squash and merge`를 권장합니다.

## 10. 병합 후 정리

```bash
git switch main
git pull origin main
git branch -d feat/detection-summary
```

GitHub PR 화면에서 원격 브랜치도 삭제합니다.

```text
Delete branch
```

다음 작업은 항상 최신 `main`에서 새 브랜치를 만들어 시작합니다.

## 11. 계약·보안 규칙

코드·계약·보안 강제 규칙은 **`docs/ai-context/01-project-rules.md`를 단일 출처**로 합니다. 사람과 AI 도구가 같은 문서를 봅니다.

원본 근거는 `docs/specifications/` 의 요구사항 정의서·시스템 설계서이며, 요약본과 충돌하면 원본이 우선합니다.
