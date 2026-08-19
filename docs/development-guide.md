# Development Guide

> [!NOTE]
> **기준: 멘토 최종 패키지 (2026-08-18).** 이전 배포본(kosa_0813 포함) 기준의
> 수치·필터값·기대값은 무효입니다. 도메인·데이터·API 기준은
> `docs/ai-context/README.md`(라우팅)와 재생성된 `01`~`07` 문서를 따릅니다.
> 이 문서의 Git·브랜치·PR·E2E 절차는 계속 유효합니다.

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
→ 팀원 Review (승인 1명 이상 필수)
→ Squash and merge
→ 로컬·원격 브랜치 정리
```

초기 공통 세팅 이후에는 `main`에 직접 commit 또는 push하지 않습니다.

### 한 작업을 처음부터 끝까지

`V4-A-4.1~4.2` Summary Tool 구현을 예로 든 전체 순서입니다. 각 단계의 상세 규칙은 아래 장에서 다룹니다.

**① Issue 생성** — 무엇을, 왜, 어디까지 하면 끝인지 먼저 적습니다.

```text
제목  [A] get_fdc_summary Tool 구현
본문  담당 영역   A
      목적       Agent 가 WAFER parameter 요약과 nullable anomaly signal을 조회할 수 있게 한다
      대상 Task  V4-A-4.1, V4-A-4.2
      대상 요구사항 FR-A-05
      완료 기준   잘못된 lot_hist_id 에 ok:false 반환, 예외 미발생
                 모델 미준비 시 규칙 summary 정상 + AnomalySignal=null
      선행 작업   V4-A-1.4, V4-CM-0.2
```

**② 작업 시작 전 상태 확인** — 작업 유실을 막습니다.

```bash
cd ~/Desktop/bistel-final
git status                       # 커밋 안 한 변경이 없는지
git switch main
git pull --ff-only origin main   # 최신 main 확보
```

**③ 브랜치 생성** — `<type>/<area>-<description>` (3장)

```bash
git switch -c feat/detection-summary-tool
```

**④ 개발과 로컬 검증** (4장)

```bash
cd backend
ruff format . && ruff check . && pytest
```

**⑤ 커밋** — `<type>: <한 줄 요약>` + 본문에 무엇을 왜 (5장)

```bash
git add backend/app/detection backend/tests/unit backend/tests/contract
git commit
```

**⑥ Push 와 PR 생성** (6·7장)

```bash
git push -u origin feat/detection-summary-tool
```

PR 제목은 `<type>: <한 줄 요약>`, 본문에 `## 변경 내용`·`## 변경 이유`가 있어야 PR Policy를 통과합니다. 담당 영역은 제목이 아니라 라벨로 표시합니다. Issue를 닫으려면 본문에 `Closes #13`을 넣습니다.

**⑦ 검사와 리뷰 통과** (8·9장) — PR Policy 자동 검사 + **팀원 1명 이상 승인**

**⑧ Squash and merge 후 정리** (10장)

```bash
git switch main
git pull --ff-only origin main
git fetch --prune origin
git branch -D feat/detection-summary-tool
```

Squash merge는 브랜치 커밋을 그대로 옮기지 않고 **새 커밋 하나를 만듭니다.** 그래서 `git branch -d`는 "병합되지 않았다"며 실패합니다. `-D`를 쓰되 **PR이 Merged 상태이고 최신 `main`에 변경이 반영된 것을 확인한 뒤** 해당 작업 브랜치에만 사용합니다.

원격 브랜치는 저장소 설정에 의해 자동 삭제됩니다.

### 언제 Issue 없이 진행해도 되나

| 상황                                  | Issue                |
| ------------------------------------- | -------------------- |
| 기능·API·Tool·화면 구현            | 만듭니다             |
| 버그 수정                             | 만듭니다             |
| 여러 명이 관련되거나 순서가 얽힌 작업 | 만듭니다             |
| 오타·문서 문구 수정, 설정 한 줄 변경 | 없이 진행해도 됩니다 |

판단 기준은 **"나중에 왜 이렇게 했는지 찾아볼 일이 있는가"** 입니다. PR 본문만으로 설명이 되면 Issue 없이 가도 됩니다.

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

| Type         | 용도                              |
| ------------ | --------------------------------- |
| `feat`     | 새로운 기능, API, Tool            |
| `fix`      | 버그 수정                         |
| `refactor` | 동작 변경 없는 코드 구조 개선     |
| `test`     | 테스트 추가·수정                 |
| `docs`     | 문서 추가·수정                   |
| `chore`    | 환경, GitHub, 패키지, Docker 설정 |

### Area

| Area            | 담당 영역                     |
| --------------- | ----------------------------- |
| `common`      | 공통 설정·DB 연결·GitHub    |
| `detection`   | A - FDC Detection             |
| `knowledge`   | B - Neo4j·RAG                |
| `agent`       | C - LangGraph·HITL·n8n      |
| `analytics`   | D - Text2SQL·통계·차트      |
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
git pull --ff-only origin main
git switch -c feat/detection-summary
```

## 4. 개발 및 로컬 검증

각 담당자는 Backend 구현뿐 아니라 실제 서버와 React 연동까지 직접 확인합니다.

### 줄바꿈 (Windows 사용자)

저장소에 `.gitattributes`가 있어 기본 텍스트 파일은 **LF**로 통일하고, Windows 전용 스크립트(`*.bat`·`*.cmd`·`*.ps1`)만 CRLF로 유지합니다. Windows에서도 별도 설정 없이 그대로 쓰면 됩니다.

`*.sh`·`Dockerfile`·`*.sql`은 컨테이너 안에서 실행되므로 CRLF가 섞이면 동작하지 않습니다. 에디터가 CRLF로 저장하지 않도록 확인하세요. VS Code는 우측 하단에 현재 파일의 줄바꿈이 표시됩니다.

변경하지 않은 파일이 `git status`에 뜨면 줄바꿈 문제입니다. 해당 파일·디렉터리만 지정해 정규화합니다. `git add --renormalize .`은 작업 중인 코드까지 전부 stage하므로 범위를 좁히세요.

```bash
git status
git add --renormalize <문제가 발생한 파일 또는 디렉터리>
git diff --cached --check
git status
```

### 커밋 전 검증

Backend 변경 시 커밋 전에 실행합니다.

```bash
cd backend
ruff format .
ruff check .
pytest
```

`pytest`는 `backend/pytest.ini`의 `addopts = -m "not e2e"`에 따라 **`e2e` 마커가 지정된 테스트를 제외하고 실행**합니다.

> 제외는 **마커 기반**입니다. `tests/e2e/`에 두더라도 `@pytest.mark.e2e`를 빼먹으면 일반 `pytest`에서 실행됩니다. 모든 E2E 테스트에 마커를 반드시 지정합니다. 경로 자동 마킹(`conftest.py`)과 공용 호스트 거부 검사는 추후 구현합니다.

### E2E 실행 (파괴적 — `kosa_agent_e2e` 전용)

Agent E2E는 학원 공용 PostgreSQL 서버의 전용 논리 DB `kosa_agent_e2e`에서만 수행합니다.
`action_history`·`agent_run`·`agent_run_alarm`·`agent_tool_call`·`approval_request`·`audit_log`·
`action_delivery`와 Checkpoint 실행 데이터만 reset하며 corrected source·reference·corpus·schema는 보존합니다.

**V4-CM-3.4·V4-C-8.2의 host·DB·token reset guard가 구현·검증되기 전에는 E2E를 실행하지
않습니다.** guard는 `kosa_agent`·`kosa_text2sql`을 대상으로 한 reset을 반드시 거부해야 합니다.

```bash
# 1. reset guard로 허용된 host + kosa_agent_e2e + 확인 token을 검증한다.
#    guard가 미구현이거나 대상이 다르면 여기서 중단한다.

# 2. E2E만 실행한다.
cd backend
pytest -m e2e
```

지켜야 할 조건입니다.

- 대상은 공용 서버 안의 **전용 논리 DB `kosa_agent_e2e`**뿐입니다. `kosa_agent`·`kosa_text2sql`을 reset하지 않습니다.
- 공용 PostgreSQL·Neo4j·n8n 컨테이너를 `docker stop`으로 멈추지 않습니다. 장애 주입은 dependency override·Tool mock·테스트 webhook으로 합니다.
- 시드 `action_history` 10건은 예시이며 조치 정답으로 사용하지 않습니다. 실제 조치는 Agent 가 런타임에 생성합니다.
- corrected source·reference·corpus·migration schema와 평가 전용 synthetic artifact를 보존합니다.

근거: 요구사항 v2.0 13장 · 시스템설계서 v2.0 14장 · WBS V4-CM-3.4, V4-C-8.2

### 테스트 계층

| 계층        | 대상                                                              | 마커            |
| ----------- | ----------------------------------------------------------------- | --------------- |
| Unit        | 요약, R03, feature, base/gated `decide_action`, sqlglot, chart 규칙 | 없음         |
| Contract    | Tool 5종 정상·오류·timeout JSON                                 | `contract`    |
| Integration | PostgreSQL Repository, Neo4j, pgvector, checkpoint, 승인 트랜잭션 | `integration` |
| E2E         | FastAPI + React + `kosa_agent_e2e` + n8n 상태·복구 시나리오       | `e2e`         |
| Evaluation  | 비지도·synthetic agreement, RAG, 관계, Text2SQL, Agent rubric     | `evaluation`  |

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

두 health 경로는 배포·운영 및 개발 진단용 내부 엔드포인트다. 사용자 업무 기능과 API 명세서의 22개 엔드포인트에는 포함하지 않는다.

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

# 커밋 전 검토 — 의도한 파일만 담겼는지, 공백 오류가 없는지 확인한다
git diff --cached --check
git diff --cached --stat
git status

git commit \
  -m "feat: implement FDC summary API" \
  -m "센서 요약 결과를 Agent와 React에서 사용할 수 있도록 조회 API와 Tool을 추가한다."
```

비밀번호, API Key, 실제 `.env`, 모델 파일(`*.joblib`), 임베딩 캐시(`backend/model-cache/`)를 커밋하지 않습니다. 반대로 `backend/artifacts/*.json` manifest는 재현성 근거이므로 **커밋합니다**.

테스트 파일은 계층별 폴더에 둡니다. 도메인별 폴더(`tests/detection` 등)를 만들지 않습니다.

목표 구조입니다. 현재 공통 Unit·Contract 테스트와 `tests/test_health.py`가 있으며, 각 담당자는 자기 파트를 구현하면서 해당 테스트 계층에 테스트를 추가합니다.

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

Commit 규칙과 같은 형식을 씁니다.

```text
<type>: <한 줄 요약>
```

`type`은 `feat`, `fix`, `refactor`, `test`, `docs`, `chore` 중 하나입니다.

**Squash merge를 쓰므로 PR 제목이 그대로 `main`의 커밋 메시지가 됩니다.** 그래서 커밋과 같은 형식이어야 히스토리가 한 가지로 유지됩니다.

예시:

```text
feat: FDC 요약 조회 API 구현
fix: 문서 검색 모델 필터 오류 수정
docs: API 계약 문서 보완
```

담당 영역은 제목에 쓰지 않습니다. 브랜치명(`feat/detection-summary`)에 이미 들어 있고, 구분이 필요하면 **라벨**을 사용합니다. 라벨은 여러 개를 붙일 수 있고 목록에서 필터할 수 있습니다.

### PR 본문

필수 항목은 두 개입니다.

```text
## 변경 내용
## 변경 이유
```

관련 Issue가 있으면 본문에 닫기 키워드를 함께 적습니다. `Closes #12`를 작성한 PR이 `main`에 병합되면 연결된 Issue가 자동으로 종료됩니다. `Fixes`, `Resolves`도 같게 동작합니다.

```text
Closes #12
```

Issue 없이 진행해도 되는 작업은 1장 「언제 Issue 없이 진행해도 되나」를 따릅니다. 그런 경우 이 항목은 비워 둡니다.

## 8. PR Policy 자동 검사

PR을 생성·수정·재오픈하거나 작업 브랜치에 추가 push하면 `PR Policy / Validate PR`이 실행됩니다.

자동 검사 항목:

- 브랜치명 형식
- PR 제목 `<type>: <한 줄 요약>`
- `## 변경 내용`
- `## 변경 이유`

PR Policy가 실패하면 `Checks`의 오류 메시지를 확인하고 브랜치 또는 PR 본문을 수정합니다. PR 본문을 수정하면 자동으로 다시 실행됩니다.

## 9. Review 및 Merge 규칙

- **다른 팀원 1명 이상의 승인(Approve)이 있어야 병합할 수 있습니다.** 문서상 권장이 아니라 `main` 브랜치 보호 규칙으로 강제됩니다.
- PR Policy가 성공한 뒤 병합합니다.
- API·Tool 계약을 변경한 경우 v2 요구사항·시스템설계서 8~9장과
  `docs/planning/V4-CM-0.3_API_Tool_영향표.md`, 해당 V4 Task를 먼저 정렬했는지 확인합니다.
- 기능 담당자가 실제 PostgreSQL·Neo4j·n8n·React 연동 결과를 PR에 기록했는지 확인합니다.
- E2E를 실행한 경우 **격리 DB에서 수행했음**을 PR에 명시했는지 확인합니다.
- 병합 방식은 `Squash and merge`로 **고정**합니다. 저장소 설정에서 merge commit과 rebase를 비활성화했으므로 PR 화면에 다른 선택지가 나오지 않습니다.

### main 브랜치 Ruleset (적용값)

**`Settings → Rules → Rulesets` 하나만 사용합니다.** 구형 `Settings → Branches`(Branch protection)와 동시에 만들면 두 규칙이 중첩되고 더 엄격한 쪽이 적용돼 원인 추적이 어려워집니다.

Ruleset 이름은 `main-protection`입니다.

| 설정                                                                | 값                          |
| ------------------------------------------------------------------- | --------------------------- |
| Enforcement status                                                  | Active                      |
| 대상 브랜치                                                         | `main`                    |
| Require a pull request before merging                               | 켬                          |
| └ Required approvals                                               | **1**                 |
| └ Dismiss stale pull request approvals when new commits are pushed | 켬                          |
| └ Require conversation resolution before merging                   | 켬                          |
| └ Allowed merge methods                                            | **Squash 만**         |
| Require status checks to pass                                       | 켬                          |
| └ 필수 검사                                                        | `PR Policy / Validate PR` |
| Require branches to be up to date before merging                    | **끔**                |
| Require linear history                                              | 켬                          |
| Block force pushes                                                  | 켬                          |
| Restrict deletions                                                  | 켬                          |
| Bypass list                                                         | **비움**              |

**`Require branches to be up to date`를 끄는 이유** — 4명이 동시에 PR을 올리면 다른 PR이 머지될 때마다 브랜치 갱신과 재승인이 반복됩니다. 각자 다른 영역을 작업하는 동안은 이득보다 마찰이 큽니다.

**Bypass는 비워 둡니다.** 예외를 두면 공통 영역 PR을 올린 사람이 자기 것을 그대로 머지하게 되어 승인 규칙이 무의미해집니다.

Actions 장애로 병합이 막히면 **해당 필수 status check만 일시적으로 제거**하고, 원인을 해결한 뒤 즉시 복원합니다. Ruleset 전체 비활성화는 최후 수단으로만 사용하며, 설정 변경 내용은 팀에 공유하고 Issue 또는 문서에 기록합니다.

**자기 PR은 자기가 승인할 수 없습니다.** 다른 팀원이 승인해야 병합 버튼이 열립니다.

`Dismiss stale approvals`를 켰으므로 승인 후 추가 push를 하면 승인이 취소됩니다. 리뷰 반영 커밋을 올렸다면 다시 승인을 받아야 합니다.

### 저장소 병합 설정 (적용값)

`Settings → General → Pull Requests`

| 설정                               | 값                                                      |
| ---------------------------------- | ------------------------------------------------------- |
| Allow squash merging               | 켬 (기본 메시지:`Pull request title and description`) |
| Allow merge commits                | **끔**                                            |
| Allow rebase merging               | **끔**                                            |
| Automatically delete head branches | 켬                                                      |

merge commit을 끄면 `main` 이력이 PR 하나당 커밋 하나로 유지됩니다. 켜져 있으면 PR 화면에서 실수로 고를 수 있고, 실제로 초기 PR 몇 건이 merge commit으로 들어갔습니다.

Ruleset의 `Allowed merge methods`와 함께 이중으로 막습니다.

### 적용 상태 확인

두 표는 실제 설정과 일치해야 합니다. 다음으로 현재 값을 확인할 수 있습니다.

```bash
gh api repos/bistel-final/bistel-final/rulesets --jq '.[].id' | while read id; do
  gh api "repos/bistel-final/bistel-final/rulesets/$id" \
    --jq '{name, enforcement, bypass: (.bypass_actors | length), rules: [.rules[].type]}'
done

gh api repos/bistel-final/bistel-final \
  --jq '{squash: .allow_squash_merge, merge: .allow_merge_commit,
         rebase: .allow_rebase_merge, autodelete: .delete_branch_on_merge}'
```

설정을 변경하면 팀에 공유하고 위 두 표를 함께 갱신합니다.

## 10. 병합 후 정리

GitHub에서 PR이 `Merged` 상태인지 먼저 확인합니다.

```bash
git switch main
git pull --ff-only origin main
git fetch --prune origin
```

`main`에 변경이 반영된 것을 확인한 뒤 작업 브랜치를 삭제합니다.

```bash
git branch -D feat/detection-summary
```

> **`-d`가 아니라 `-D`인 이유** — Squash merge는 브랜치 커밋을 그대로 옮기지 않고 **새 커밋 하나를 만듭니다.** Git 입장에서 원래 브랜치는 "병합되지 않은" 상태라 `-d`가 거부합니다.
>
> `-D`는 병합 여부를 확인하지 않고 지우므로, **PR이 Merged이고 최신 `main`에 변경이 들어온 것을 확인한 뒤 해당 작업 브랜치에만** 사용합니다.

원격 작업 브랜치는 PR 병합 후 저장소 설정(`Automatically delete head branches`)에 의해 자동 삭제됩니다. 자동 삭제되지 않은 경우에만 PR 화면의 `Delete branch`를 사용합니다.

다음 작업은 항상 최신 `main`에서 새 브랜치를 만들어 시작합니다.

## 11. 계약·보안 규칙

코드·계약·보안의 기준 순서는 **멘토 최종 패키지(8/18) → 요구사항 v2.0 → 시스템설계서
v2.0 → 역할분담 v10.0 → WBS v4** 입니다. `docs/ai-context/README.md`가 라우팅 인덱스이며,
`01`~`07`·`PROMPT_TEMPLATE`·`tasks/*` 는 최종 패키지 기준으로 재생성 완료(2026-08-18) 상태라
구현 근거로 사용할 수 있습니다.

### API 계약을 바꿀 때

> 외부 API 계약(경로·필드명)의 정본은 **멘토 패키지 `02_화면별_API_가이드.md`** 입니다.
> DTO 역산 명세서는 내부 상세 문서이며, 멘토 확정 6필드와 충돌하면 멘토 스펙이 이깁니다.

API 명세서는 손으로 쓰지 않습니다. Backend Pydantic DTO에서 역산해 CSV·Markdown·PDF 세 형식을 함께 생성합니다. 따라서 순서를 지켜야 세 문서가 어긋나지 않습니다.

```bash
# 1. 원본 스펙을 먼저 고친다 (요구사항 → 시스템설계서 순)
# 2. Backend DTO 를 고친다
#    backend/app/{common,detection,knowledge,agent,analytics}/schemas.py
# 3. 계약 테스트로 확인한다
cd backend && pytest tests/contract -q

# 4. 명세서 세 형식을 다시 만든다
cd .. && source .venv/bin/activate
pip install -r docs/deliverables/api/requirements.txt   # 최초 1회 (reportlab)
python docs/deliverables/api/build_api_spec.py

# 5. CSV·Markdown·PDF 세 개를 한 커밋에 함께 담는다
git add docs/deliverables/api/API명세서.{csv,md,pdf}
```

- **생성 결과를 직접 편집하지 않습니다.** 고칠 것이 있으면 DTO 또는 생성기를 고치고 다시 만듭니다.
- 세 형식 중 하나만 커밋하면 나머지 둘이 옛 계약을 가리킵니다. 반드시 함께 담습니다.
- `reportlab`은 문서 도구 전용 의존성이라 `backend/requirements.txt`에 넣지 않습니다.

자세한 절차와 글꼴 설정은 `docs/deliverables/api/README.md`에 있습니다.
