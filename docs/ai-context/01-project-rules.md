# 01. 프로젝트 강제 규칙

> 기준 원천: 멘토 최종 패키지 (2026-08-18) · 요구사항 v2.0 · 시스템설계서 v2.0 · WBS v4
> 마지막 동기화: 2026-08-18

어기면 리뷰에서 반려되는 규칙만 적는다. 배경 설명은 원본(README 라우팅)으로 간다.

---

## 1. 데이터·사양 기준

- 데이터 정본은 **멘토 최종 패키지 `sample/data/`** 다. kosa_0813 이하 구본 기준의
  수치·필터값·기대값은 전부 무효다 (실측 기준은 `02-domain-rules.md` 1장).
- `area` 값은 `Photo`/`Etch` 다. 소문자 비교가 필요하면 쿼리 쪽에서 정규화한다.
- `lot_history.fault_code` 는 평가 전용이다. Agent 판단 입력·프롬프트·Tool 결과·
  Text2SQL 응답 가공에 넣지 않는다.
- 금지 용어: `sensor`(→ 파라미터) · `judgement`(→ alarm_type) · `SPC`.
  코드 식별자·API 필드·화면 문구·문서 전부에 적용한다.

## 2. 불변 계약

- **Tool 반환 형식은 `{ok, ..., reason}`** 이다. 실패 reason 은
  `POLICY_REJECTED:` `LLM_NOT_READY:` `DEPENDENCY_ERROR:` 등 접두어 규칙을 따른다
  (`app/common/tool_contracts.py` 가 정본).
- **`langgraph==0.2.53` 고정.** 올리지 않는다.
- **`decide_action` 은 규칙 기반 순수 함수**다. LLM·DB 접근을 넣지 않는다.
  `anomaly_score` 는 조치 규칙에 직접 반영하지 않는다.
- 조치 어휘는 **MONITORING / WARNING / EQP_HOLD 3단계**뿐이다.
  LOT_HOLD·NOTIFY·MONITOR 는 폐어다.
- EQP_HOLD 만 사람 승인(HITL)이며, 승인 후에만 MES(Kafka `fdc.actions`)로 나간다.

## 3. DB 접근

- LLM 이 생성한 SQL 은 **`kosa_readonly`(SELECT 전용) pool 로만 실행**한다.
- 질의 로그 기록은 **`kosa_query_logger`(nl_query_log INSERT 전용)** 만 쓴다.
- 계정·권한의 정본은 `backend/migrations/002_analytics_roles.sql`.
  pool 은 `app/analytics/db_pool.py` 가 계정을 강제하며, 다른 계정 DSN 은 기동이 거부된다.
- 공용 DB 쓰기 작업(계정·스키마·적재)은 실행 전 팀 공유가 필수다.

## 4. Git·PR

CI(`.github/workflows/pr-policy.yml`)는 아래 3개만 검사한다. lint·test 는 CI 가
돌리지 않으므로 **검증 결과를 PR 본문에 수동 기록**한다.

```
PR 제목    ^(feat|fix|refactor|test|docs|chore): .+$      (대괄호 태그 금지)
브랜치     ^(feat|fix|refactor|test|docs|chore)/(common|detection|knowledge|agent|analytics|integration)-[a-z0-9-]+$
PR 본문    "## 변경 내용" 과 "## 변경 이유" 문자열 필수
```

- main 직접 커밋 금지. squash merge. 자기 승인 금지.
- **`ruff format` → `git add`** 순서. 반대로 하면 검증한 코드와 커밋이 어긋난다.
- `git add -A` 금지. 경로를 명시한다.
- 커밋 전 검증: `cd backend && ruff format . && ruff check . && pytest`
- PR 본문 필수 항목: 담당자 · V4 Task ID · Closes #이슈 · 실행 명령과 결과 ·
  관련 FR · artifact 경로 · 미검증 사항.

## 5. 로컬 환경

- `.env` 는 `.env.example` 기준으로 만들고 DSN 5종을 채운다
  (`postgresql+psycopg://` 접두 필수).
- bootstrap 테스트가 `NotRegisteredError: active corrected build` 로 실패하면:
  `MENTOR_PACKAGE_DIR` 설정 후
  `python3 scripts/build_corrected_dataset.py --archive <zip> --confirm`.
  (신본 전환 시 이 절차의 대상 아카이브가 교체된다 — Common 공지 확인)

## 6. AI 도구 사용

- 진입점은 `CLAUDE.md`(Claude Code) · `AGENTS.md`(Codex)이며 내용이 동일해야 한다.
- 작업 요청에는 담당자와 `V4-*` Task ID 를 명시한다.
- README 상태 표에서 ✅ 가 아닌 요약 문서의 본문을 근거로 쓰지 않는다.
