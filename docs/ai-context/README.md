# AI 작업 문서

> 기준 원천: 멘토님 제공 최종 `project.zip`(2026-08-18)
> 기준 요구사항: v2.1 작업본
> 기준 시스템설계서: v2.1 작업본
> 기준 역할분담: v10.1 작업본
> 기준 API: v3 작업본
> WBS: v5 작업본
> 마지막 동기화: 2026-08-20

> [!CAUTION]
> **FINAL-DOC.** 최종 데이터 기준 문서의 교차검토를 완료했다. `kosa_0813`, 요구사항·설계 v2.0 이하/
> 역할 v10.0 이하/WBS v4 이하,
> `01`~`07`과 `PROMPT_TEMPLATE.md`는 이전 epoch 이력이다.
> WBS v5와 역할별 Task 문서(`tasks/*.md`)는 최종 기준으로 정합화됐다. 문서 검토 완료가 구현
> 완료를 뜻하지 않으므로 실제 진행 상태는 각 `V5-*` Task의 선행 게이트와 완료 기준으로 판단한다.

이 문서는 AI 코딩 도구와 팀원이 같은 최종 데이터·계약을 읽도록 하는 라우팅 인덱스다.
패키지 전체 참고 구현을 저장소에 병합하지 않고 검증된 기준표와 새 원본 문서만 사용한다.

## 1. 문서 우선순위

충돌하면 위쪽이 이긴다.

```text
0. docs/reference/mentor-final-20260818/README.md                 최종 ZIP 해시·실측값·충돌 우선순위
1. docs/specifications/요구사항정의서_v2_1_작업본.md             사용자 동작·업무 규칙·수용 기준
2. docs/specifications/시스템설계서_v2_1_작업본.md              구현·데이터·상태 전이 계약
3. docs/specifications/FDC_프로젝트_역할분담_v10_1_작업본.md     담당·소유권·완료 범위
4. docs/deliverables/api/API명세서_v3_작업본.md                 외부 최소 호환·확장 API
5. docs/planning/Task분해_WBS_v5_작업본.md                     V5-* Task ID·선행관계
5-1. docs/ai-context/tasks/{A,B,C,D}-*.md                     역할별 Task·완료 기준·주의
6. 코드
```

구현 요청에는 현재 WBS v5 작업본에 실재하고 리뷰를 통과한 `V5-*` Task ID를 명시한다. 임의의
Task ID를 만들지 않으며, 선행 게이트나 상위 계약과 충돌하면 해당 Task를 확정된 것으로 간주하지
않고 먼저 보고·정합화한다.

## 2. 최종 데이터 불변값

| 항목 | 기준값 |
|---|---|
| dataset epoch | `fdc_final_20260818` |
| ZIP SHA-256 | `e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3` |
| base table | 9개 |
| Trace·Summary·evaluation | 14,400 · 4,800 · 4,800 |
| evaluation 분포 | IN 4,538 · OOC 216 · OOS 46 |
| 저장 알람 | TRACE 138 · SUMMARY 51 · 합계 189 |
| 파생 R03 | 3 · 명시 포함 합계 192 |
| incident·참고 action | 12 · MONITORING 5 / WARNING 4 / EQP_HOLD 3 |
| metrology | 48 · PASS 39 / FAIL 9 |
| Neo4j | 44 nodes · 85 relationships |
| AREA·recipe | `Photo`, `Etch` · `RECIPE01`, `RECIPE02`, `RECIPE03`, `RECIPE04` |

최종 패키지의 `docs/04_알람_재현_가이드.md`와 `docs/05_검토질문_답변.md`에 적힌
126/47/42/10은 오래된 수치다.
물리 CSV·DDL·byte-identical Generator 결과를 우선한다.

## 3. 평가·조치 원칙

```text
public_fault_ground_truth_available=true
label_source=SYNTHETIC_GENERATOR
production_ground_truth_available=false
usage_scope=EVALUATION_ONLY
```

- raw `fault_code`: NRM 554 / FOC 15 / RFM 12 / MFD 13 / TMD 2 / OTH 4
- 알람 Agent 가설: `FOC|RFM|MFD|TMD|OTH`; `NRM`은 고장 가설이 아니다.
- 라벨·Generator 주입 정보는 모델·Agent Runtime 입력에서 격리한다.
- anomaly score는 설명·화면의 보조 근거다. 조치·incident·HITL 결정에 사용하지 않는다.
- SUMMARY OOC-only → MONITORING
- TRACE OOS, strict R03 없음 → WARNING + n8n SMTP
- strict R03 → EQP_HOLD + 승인 요청 이메일 + 승인 후 Kafka MES Mock

## 4. 화면·API 경계

최종 패키지 5개 화면을 canonical 사용자 영역으로 둔다.

1. 알람 대시보드
2. 알람·Trace
3. Agent 분석(승인·실행·감사 탭)
4. 문서 검색
5. Ontology

필수 public 업무 API는 **11개**다. API 명세 v3의 source 호환 9개(`POST /agent/ask` 포함),
보안 필수 `GET /relations/chambers/{chamber_id}` 1개, 실행 `POST /agent/runs` 1개로 구성한다.
Ontology 응답은 선택 chamber의 subgraph와 화면·Agent가 함께 쓰는 context를 담는다.
`POST /internal/actions/{action_id}/delivery`는 n8n·Kafka 결과 write-back용 필수 internal
callback이며 Frontend 업무 API가 아니다. `/health`·`/health/ready`도 업무 API·화면 수에서
제외한 내부 운영·진단 scope다. Text2SQL·Analytics와 기존 상세·페이지네이션·재시도·평가 API는
필수 계약을 깨지 않는 선택 확장으로만 유지한다. 기존 8개 route family는 새 요구사항에서 명시한
adapter 또는 확장 경로가 아니면 구현 근거가 아니다.

## 5. 데이터·인프라 전환 상태

| 항목 | 상태 |
|---|---|
| 최종 ZIP 실측·해시 검증 | 완료 |
| 요구사항·설계·역할·API 새 기준본 | 교차검토 완료, V5 Task 기준 구현 |
| WBS v5·역할별 Task | 교차검토 완료, 선행관계·완료 기준 확정 |
| source intake·epoch·manifest | `V5-CM-1.1` intake, `V5-CM-1.2` epoch 발급, `V5-CM-1.3` source manifest v4, `V5-CM-1.4` Generator 재현은 기술 완료. `V5-CM-1.5`가 구 corrected 공개 실행 경로를 명시 차단하며 final DB profile marker는 후속 bootstrap Task 전이므로 미생성 |
| PostgreSQL 격리 적재 검증 | 미실행 |
| Neo4j 44/85 safe load 검증 | 미실행 |
| RAG 문서 3종·chunk·1024차원 vector 적재/검색 검증 | 미실행 |
| 공용 DB 전환 | 미실행 |

최종 ZIP의 DDL·Cypher를 공용 DB에 직접 실행하지 않는다. `master.cypher`의 전체 삭제 문장은
기존 destructive-safe loader가 차단해야 한다.

공용 PostgreSQL·Neo4j·n8n은 외부 canonical 서비스다. 팀 compose에는
Backend·Frontend·Kafka·MES Mock만 포함하며, 두 번째 DB·Neo4j·n8n을 기동하거나 readiness의
대체 성공값으로 사용하지 않는다.

RAG content는 최종 패키지의 문서 3종을 기준으로 하고, 이전 교육생 패키지에서는 누락된
`document`·`document_chunk` 스키마와 loader의 필요한 부분만 출처·해시를 고정해 보완한다.
원본은 불변으로 보존하고 corrected artifact는 별도로 생성한다. corpus revision, `ACTIVE` 전환,
overlay 또는 병렬 corpus 테이블은 두지 않는다.

`/health/ready`는 PostgreSQL epoch·schema·role, reference migration marker, Neo4j 44/85 marker,
RAG 필수 문서 3종·vector non-null·1024차원·검색 smoke, n8n, Kafka metadata·필수 topic을 실제
의존성별로 검증한다. `/health`는 외부 의존성과 무관한 process liveness만 200으로 반환한다.

## 6. 사용 중지 문서

| 문서 | 상태 |
|---|---|
| 요구사항·설계 v2.0 이하 | 이전 epoch 이력 |
| 역할분담 v10.0 이하·WBS v4 이하 | 이전 epoch 이력, v5 영향 분석 입력만 허용 |
| 신규데이터_정답라벨제거_전환기획_v1 | no-GT 전환 이력 |
| `01`~`07` | 이전 요약, 재생성 전 사용 금지 |
| `PROMPT_TEMPLATE.md` | 이전 Task 템플릿, 재생성 전 사용 금지 |
| API v2.1 MD·CSV·PDF | 이전 계약 |
| 요구사항·설계 PDF | 이전 제출본 |

## 7. AI 협업·검증 방식

- Task 완료는 특정 AI 제품·모델·리뷰 횟수가 아니라 WBS 완료 기준, 테스트, diff,
  독립 검증 증적으로 판정한다.
- 기본 흐름은 계획 작성 → 독립 계획 리뷰 → 구현·자체 검증 → 독립 구현 리뷰 →
  최종검증이다. 구현자와 검증자의 관점을 분리하되 동일 도구 내 다른 모델·서브에이전트
  교차검증도 허용한다.
- `V5-CM-1.1`·`1.2`는 Claude 내 Fable·Opus 교차검증과 실증을 통과한 완료 작업으로
  인정하며 AI 조합만을 이유로 소급 재작업하지 않는다. `V5-CM-1.3`도 구현·독립 리뷰·
  최종검증·CI를 통과한 완료 상태다.
- 계획·리뷰 메모는 작업 지원 자료이며 WBS·Task 정본을 대체하지 않는다. 범위·선행관계·
  완료 기준이 바뀐 경우에만 WBS와 Task 문서를 함께 수정한다.

## 8. 다음 작업

1. 다음 공통 작업은 `V5-CM-1.4` Generator 재현 검증이다. `V5-CM-1.1`~`1.3`을 소급
   재작업하지 않고 머지된 intake·epoch·source manifest v4를 선행 증적으로 사용한다.
2. 선행 게이트를 통과한 뒤 `V5-CM-2.*` fresh bootstrap을 `kosa_agent_e2e` → `kosa_agent` → `kosa_text2sql`
   순서로 적용한다.
3. 역할별 실제 해금 시점은 리뷰 완료된 WBS v5의 선행관계와 게이트 표를 따른다. 이 인덱스의
   요약 문구로 Task를 앞당기지 않는다.
4. 공용 DB는 각 단계에서 preflight → rehearse → apply → 재실행 no-op → 검증을 통과해야
   다음 DB로 넘어간다.

`CLAUDE.md`와 `AGENTS.md`는 이 문서를 진입점으로 사용하며 byte-identical이어야 한다.
