# 07. 테스트 가이드

> [!CAUTION]
> **사용 중지 — 아래 본문은 v1.9/v1.10/v9.6 기준의 구 이력이며 구현 근거로 사용하면 안 됩니다.**
> v2 요약 문서가 재생성되기 전에는 `docs/specifications/요구사항정의서_v2_0_작업본.md`,
> `docs/specifications/시스템설계서_v2_0_작업본.md`,
> `docs/specifications/FDC_프로젝트_역할분담_v10_0_작업본.md`와
> `docs/planning/Task분해_WBS_v4_작업본.md`의 해당 `V4-*` Task만 사용하십시오.
> 아래 본문은 참고·복사·프롬프트 입력을 금지합니다.

> 기준 요구사항: v1.9 / 시스템설계서: v1.10 / 역할분담: v9.6
> 마지막 동기화: 2026-08-12

담당별 테스트 항목은 `tasks/*.md`에 있다. 이 문서는 **공통 실행 방법과 격리 규칙**을 다룬다.

---

## 1. 테스트 계층

| 계층 | 대상 | 마커 |
|---|---|---|
| Unit | 요약, R03, feature, `decide_action`, sqlglot, chart 규칙 | 없음 |
| Contract | Tool 5종 정상·오류·timeout JSON | `contract` |
| Integration | PostgreSQL Repository, Neo4j, pgvector, checkpoint, 승인 트랜잭션 | `integration` |
| E2E | FastAPI + React + DB + n8n 골든 시나리오 | `e2e` |
| Evaluation | ML, Fault 분류, RAG, 관계, Text2SQL, Level 1·2 | `evaluation` |

```
backend/tests/
├── unit/  ├── contract/  ├── integration/  ├── e2e/  └── fixtures/
```

목표 구조다. 현재 공통 Unit·Contract 테스트와 `tests/test_health.py`가 있으며 각 담당자가 자기 파트를 구현하며 확장한다.
**도메인별 폴더(`tests/detection` 등)를 만들지 않는다.**

---

## 2. 실행

```bash
cd backend
ruff format .
ruff check .
pytest              # e2e 마커 제외

cd ../frontend
npm run lint
npm run build
```

`pytest`는 `backend/pytest.ini`의 `addopts = -m "not e2e"`를 따른다.

API contract 테스트는 Text2SQL 정책 거부가 HTTP 200 + `is_rejected=true`로, 요청 body 형식 오류가 422로 분리되는지 확인한다.

> **마커 기반이다.** `tests/e2e/`에 두더라도 `@pytest.mark.e2e`를 빼먹으면 일반 `pytest`에서 실행된다.
> 모든 E2E 테스트에 마커를 반드시 지정한다. 경로 자동 마킹(`conftest.py`)과 공용 호스트 거부 검사는 추후 구현한다.

GitHub Actions는 Ruff·pytest·실제 서버 연결을 실행하지 않는다. `PR Policy / Validate PR`만 돈다.
**담당자가 로컬에서 실행하고 결과를 PR에 기록한다.**

---

## 3. E2E 격리 (파괴적)

E2E는 다음을 **비운 상태**를 전제한다.

```
action_history  agent_run  agent_run_alarm  agent_tool_call
approval_request  audit_log  action_delivery  운영 nl_query_log
+ Checkpoint 실행 데이터 (checkpoint_migrations 는 보존)
```

유지하는 것: `fdc_alarm` 51건 등 입력 6종, 기준정보, 문서 3·청크 39.

**격리 DB 검증 장치가 구현되기 전까지 공용 서버에 연결된 `.env` 상태에서 E2E를 실행하지 않는다.**
`backend/scripts/reset_agent_e2e_db.py`는 아직 미구현이며, DB명 검사만으로는 공용 DB를 구분할 수 없으므로 호스트까지 직접 확인한다.

```bash
# 1. 접속 대상이 격리 DB인지 눈으로 확인. 공용 서버면 중단
# 2. E2E만 실행
cd backend && pytest -m e2e
```

지켜야 할 조건이다.

- 대상은 **격리 Compose DB 또는 전용 테스트 DB**다
- 공용 PostgreSQL·Neo4j·n8n 컨테이너를 `docker stop` 하지 않는다. 장애 주입은 dependency override·Tool mock·테스트 webhook으로 한다
- `ACT-0001~0010`은 DB에 적재하지 않고 `backend/tests/fixtures/expected_actions.json`으로 비교한다. 원본 `ACT-0002`의 `APPROVED/SENT` 상태를 복사하지 않는다
- 공용 DB의 초기 정답 데이터를 삭제하거나 덮어쓰지 않는다

### 두 DB의 초기 상태가 다르다

| 환경 | 유지 | 시작 시 비움 | 용도 |
|---|---|---|---|
| Text2SQL 평가 DB (`kosa_text2sql`) | 배포 원본 전체 + ACT-0001~0010 | 없음 | 골드 12문항·방어 6종 |
| Agent E2E DB (`kosa_agent`) | 기준정보·입력 6종·문서 3·청크 39 | 실행 데이터 전부 | 골든 시나리오·51건 배치 |
| 단위 테스트 | fixture별 최소 입력 | fixture별 격리 | 결정표·SQL 검증 |

fresh E2E 런타임 기대값: **자동조치 7건 `AUTO/SENT`, EQP_HOLD 3건 `PENDING/WAITING`, approval_request PENDING 3건.**

---

## 4. 장애 주입

| 대상 | 방법 |
|---|---|
| Neo4j 장애 | dependency override로 실패 Repository 또는 잘못된 URI |
| Tool timeout | 테스트 Tool이 제한시간 초과 |
| n8n 명확한 실패 | 별도 테스트 webhook이 HTTP 500 |
| n8n 응답 유실 | `action_delivery` 기록 후 응답만 timeout |

통합 장애 테스트는 루트 `docker-compose.test.yml` override에서 `n8n-stub`과 격리 테스트 DB를 쓴다.
기본 `docker-compose.yml`의 공용 서비스 이름·볼륨을 재사용하지 않는다.

**정상 E2E 1~3은 실제 API·기준 데이터를 쓰고 UI·Backend Mock을 쓰지 않는다.**
장애 시나리오 4-A·4-B만 통제된 실패 주입을 위한 Mock을 허용한다. (NFR-18)

---

## 5. 골든 시나리오 4건

| # | 입력 | 검증 |
|---|---|---|
| 1 자동조치 | ALM-0001 | `AUTO`, `approved_by='system'`, `approved_at=created_at`, 최종 `SENT`, COMPLETED |
| 2 승인 | ALM-0022 | `PENDING/WAITING` + approval PENDING 생성 → 승인 시 기존 행 갱신, `approved_by=decided_by`, n8n 모의 전송 정확히 1회 |
| 3 반려 | ALM-0048 | 기존 `PENDING/WAITING` action_history를 새 행 생성 없이 `REJECTED/CANCELED`로 갱신, 승인자 NULL 유지, n8n 미호출 |
| 4-A Tool 장애 | 격리 주입 | Tool `{ok:false}` 반환, `agent_tool_call=ERROR|TIMEOUT`, 상한 후 FAILED, **조치 전송 이전이므로 `action_history` 행과 `ACTION_SEND_FAILED` 없음** |
| 4-B n8n 장애 | 테스트 webhook 500·timeout | **승인 트랜잭션은 롤백되지 않음**, `send_status=FAILED`, 동일 action_id 재시도에서 중복 전송 없음 |

> 순수 연쇄 이상은 유일 케이스(LOT-260008 w1)에 트리거 알람이 없어 E2E가 불가능하다.
> 골든 시나리오에 넣지 않고 `decide_action` 단위 테스트로만 검증한다.

---

## 6. 평가 artifact 규격

```
docs/evaluation/{detection,knowledge,agent,analytics}/<run_id>.json|csv
```

**공통 기록 항목** — provider·model·prompt/규칙 버전·실행 설정·원본 fixture SHA-256

**Text2SQL 항목별 필드** (설계 9.8)

```
case_type      GOLD | DEFENSE
case_id        Q01..Q12 | D1..D6
question, generated_sql, attempt_count
expected_result, actual_result
expected_visualization, actual_visualization
passed, reason, latency_ms
```

대용량 rows는 결과 해시와 핵심 비교값만 저장해 파일 크기를 제한한다.

평가 실행은 별도 `evaluation` profile one-shot이 `docs/evaluation`을 `rw`로 마운트해
**원자적 임시파일 → rename** 방식으로 기록한다. 일반 API 프로세스는 `ro`로 읽기만 한다.

---

## 7. PR 기록

```
- 실행한 테스트 명령과 결과
- 요구사항 ID별 충족 여부
- 결과 artifact 경로와 관련 PR 또는 Notion Task 링크
- 실제 PostgreSQL·Neo4j·n8n·React 연동 결과
- E2E를 실행했다면 격리 DB에서 수행했음을 명시
- 미완료·미검증 항목 (숨기지 않는다)
```

`grep` 출력이나 파일 존재 확인은 탐색 증거일 뿐 완료 증빙이 아니다. Task 완료는 위 기록과 해당 계층의 실제 테스트 결과가 함께 있을 때만 인정한다.

---

## 원본 절

```
설계 14.1  DB 상태 분리·role 권한   설계 14.2  장애 주입
설계 15.1  테스트 계층·평가 artifact
설계 15.2  핵심 설계 검증 (기대 결과 표)
설계 15.3  요구사항 추적
요구사항 13장 테스트 요구사항·데이터 격리 원칙 · 부록 B 골든 시나리오 · NFR-18
```
