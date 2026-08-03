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

커밋 전에 실행합니다.

```bash
ruff format .
ruff check .
pytest
```

필요한 경우 FastAPI를 실행해 실제 연동을 확인합니다.

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

확인 경로:

```text
http://localhost:8000/docs
http://localhost:8000/health
http://localhost:8000/health/ready
```

GitHub Actions에서는 Ruff·pytest·실제 서버 연결을 실행하지 않습니다. 실행 결과는 PR의 `확인 방법`과 체크리스트에 기록합니다.

## 5. Commit 규칙

### 형식

```text
<type>: <한 줄 요약>
```

본문에는 무엇을 왜 변경했는지 작성합니다.

### 예시

```bash
git add app/detection tests/detection docs/contracts.md

git commit \
  -m "feat: implement FDC summary API" \
  -m "센서 요약 결과를 Agent와 React에서 사용할 수 있도록 조회 API와 Tool을 추가한다."
```

비밀번호, API Key, 실제 `.env`, 모델 파일(`*.joblib`)을 커밋하지 않습니다.

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
- API·Tool 계약을 변경한 경우 `docs/contracts.md`가 함께 수정됐는지 확인합니다.
- 기능 담당자가 실제 PostgreSQL·Neo4j·n8n·React 연동 결과를 PR에 기록했는지 확인합니다.
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

- Tool 오류는 예외 대신 `{"ok": false, "reason": "..."}`로 반환합니다.
- REST API는 상황에 맞는 HTTP 상태코드(`404`, `409`, `422`)를 사용합니다.
- API 또는 Tool 계약 변경은 코드, 테스트, `docs/contracts.md`를 같은 PR에서 수정합니다.
- 실제 `.env`, 비밀번호, API Key, 개인 이메일, 서버 접속정보를 Git에 올리지 않습니다.
- Text2SQL은 반드시 `kosa_readonly` 연결을 사용합니다.
