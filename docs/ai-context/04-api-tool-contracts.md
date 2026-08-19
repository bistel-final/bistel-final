# 04. API · Tool 계약

> 기준 원천: 멘토 최종 패키지 `02_화면별_API_가이드.md` (정본, 2026-08-18)
> 보조 기준: 시스템설계서 v2.0 8~9장 · `app/common/tool_contracts.py` · `app/*/schemas.py`
> 마지막 동기화: 2026-08-18

---

## 1. 공통 규칙 (멘토 확정)

```
날짜 필터    date_from · date_to (YYYY-MM-DD)
area         필수 · 단일값 ('Photo' | 'Etch')      equipment · chamber 선택
응답 시각    YYYY-MM-DD HH:MM:SS
wafer 파라미터  전체 식별자 문자열 (LOT001W003)
조회 원천    PostgreSQL. Neo4j 는 상류/토폴로지 판별에만
```

> 프론트·백 필드명 불일치는 **조용한 0건**으로 나타난다. 위 이름을 그대로 쓴다.

## 2. 화면별 확정 API (최소 구현 = 골든 시나리오)

| 화면 | 담당 | API |
|---|---|---|
| 1 알람 대시보드 | A | `GET /alarms` 한 번으로 4위젯+KPI (클라이언트 집계) · Agent 분류 위젯은 `GET /agent/runs` 공유 |
| 2 알람 | A | `GET /alarms` 재사용 · `GET /trace` · `GET /parameters` |
| 3 Agent 분석 | C | `GET /agent/runs` · `GET /approvals` · `POST /approvals/{id}/decision` · `GET /audit-logs`(D와 협의) |
| 4 문서 검색 | B | `POST /documents/search` |
| 5 자연어 질의 | **D** | `POST /analytics/query` |

**핵심 스펙 요약**

```
GET /alarms      trace_alarm(OOS) + summary_alarm(OOC) UNION ALL, source=TRACE|SUMMARY
                 (제공 SQL 그대로 — summary 쪽 seq_no 는 NULL, value 자리는 stat_value)
GET /trace       lot·wafer(wafer_id)·chamber·parameter 로 raw 시계열, measured_at 순
GET /parameters  SELECT * FROM dim_parameter — 한계선 단일 출처
GET /agent/runs  action_history 1행 = 실행 1건. fault_code 는 대표 parameter →
                 고장모드 매핑 (FOC/RFM/MFD/TMD, 매핑 불가 시 OTH).
                 tools:[{name,status}] · latency_ms · llm_model 은 실제 실행 로그
GET /approvals   approval_required='Y'(=EQP_HOLD) 만. 승인 시 n8n MES 워크플로 호출
GET /audit-logs  action_history 파생 — actor AGENT|HUMAN|SYSTEM ·
                 event ACTION_RECOMMEND|NOTIFY|APPROVE|REJECT|SEND · 시각은 각 *_at
POST /documents/search   body {query, model_code?, top_k}
```

## 3. D — `POST /analytics/query` (화면 5)

**멘토 확정 응답 (외부 계약):**

```
POST /analytics/query   body { question }
→ 200 { generated_sql, columns, rows, is_valid, is_rejected, reject_reason }
```

- 정책 거부(비허용 테이블·DML 등)도 **HTTP 200** + `is_rejected=true` + `reject_reason`.
- 화이트리스트 SELECT 만 허용, DML 거부 — 검증기는 `app/analytics/sql_validator.py`.
- 멘토 예시 질의 (회귀 질문셋 시드): "챔버별 OOS 알람 수" · "파라미터별 알람 수" ·
  "승인 대기 조치".

**내부 확장 (상위 호환 — 멘토 필드를 유지한 채 추가):**

```
outcome (4상태: SUCCESS | POLICY_REJECTED | INVALID | DEPENDENCY_ERROR)
metric · visualization(chart_type/x/y) · statistics · latency_ms
```

정본 DTO 는 `app/analytics/schemas.py`. 멘토 6필드는 이름·의미를 바꾸지 않는다.
차트 유형 결정은 서버 확장 필드로 내려주되, 클라이언트가 무시해도 화면이 성립해야 한다.

## 4. Tool 계약 (Agent 내부)

- 모든 Tool 반환은 **`{ok, ..., reason}`**. 예외를 밖으로 던지지 않는다.
- 실패 reason 접두어: `POLICY_REJECTED:` · `LLM_NOT_READY:` · `DEPENDENCY_ERROR:` 등
  (`app/common/tool_contracts.py` 의 REASON_PREFIXES 가 정본).
- D 의 `generate_analysis_plan` 은 `AnalysisPlanToolInput/Result` 계약을 따른다
  (성공 시 sql·metric·visualization 필수).
- Tool 호출 예산·재시도 규칙은 설계 6.6 · 요구사항 FR-C-08.

## 5. HTTP 상태 규약 (내부 API 공통)

```
200   정상 + 정책 거부 (거부는 본문 필드로 표현)
422   malformed 입력 (필수 필드 누락·형식 오류)
503   의존성 실패 (LLM·DB 연결 불가)
```
