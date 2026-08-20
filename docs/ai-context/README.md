# AI 작업 문서

> 기준 원천: 멘토님 제공 최종 `project.zip`(2026-08-18)
> 기준 요구사항: v2.1 작업본
> 기준 시스템설계서: v2.1 작업본
> 기준 역할분담: v10.1 작업본
> 기준 API: v3 작업본
> WBS: v5 작업본
> 마지막 동기화: 2026-08-19

> [!CAUTION]
> **FINAL-DOC.** 최종 데이터 기준 문서를 재기준화하고 있다. `kosa_0813`, 요구사항·설계 v2.0 이하/
> 역할 v10.0 이하/WBS v4 이하,
> `01`~`07`과 `PROMPT_TEMPLATE.md`는 이전 epoch 이력이다.
> WBS v5와 역할별 Task 문서(`tasks/*.md`)는 최종 기준으로 재작성됐다.

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

WBS v5가 확정됐으므로 구현 요청에는 WBS v5에 실재하는 `V5-*` Task ID를 명시한다. 임의의
Task ID를 만들지 않는다. `FINAL-DOC` 표시는 WBS v5 확정 이전 문서 정리 이력에만 남는다.

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

필수 호환 API는 API 명세 v3의 9개 endpoint이며 Agent 자연어 질의는 `POST /agent/ask`다.
Ontology 화면은 이 9개와 별도로 보안 필수 public API인 `GET /relations/chambers/{chamber_id}`만 사용한다.
`POST /internal/actions/{action_id}/delivery`는 n8n·Kafka 결과 write-back용 필수 internal
callback이며 Frontend 업무 API가 아니다. `/health`·`/health/ready`도 업무 API·화면 수에서
제외한 내부 운영·진단 scope다. Text2SQL·Analytics와 기존 상세·페이지네이션·재시도·평가 API는
필수 계약을 깨지 않는 선택 확장으로만 유지한다. 기존 8개 route family는 새 요구사항에서 명시한
adapter 또는 확장 경로가 아니면 구현 근거가 아니다.

## 5. 데이터·인프라 전환 상태

| 항목 | 상태 |
|---|---|
| 최종 ZIP 실측·해시 검증 | 완료 |
| 요구사항·설계·역할·API 새 기준본 | 완료 (v2.1·v10.1·API v3) |
| WBS v5·역할별 Task | 완료 |
| source/corrected/profile manifest | 구 `kosa_0813` 상태, 재생성 필요 |
| PostgreSQL 격리 적재 검증 | 미실행 |
| Neo4j 44/85 safe load 검증 | 미실행 |
| 공용 DB 전환 | 미실행 |

최종 ZIP의 DDL·Cypher를 공용 DB에 직접 실행하지 않는다. `master.cypher`의 전체 삭제 문장은
기존 destructive-safe loader가 차단해야 한다.

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

## 7. 다음 작업

1. ~~새 요구사항·설계·역할·API 확정~~ · ~~WBS v5와 역할별 Task 문서 작성~~ 완료
2. `V5-CM-1.*` 최종 source intake·epoch·manifest부터 구현한다.
3. `V5-CM-2.*` fresh bootstrap을 `kosa_agent_e2e` → `kosa_agent` → `kosa_text2sql`
   순서로 적용한다.
4. 착수 게이트는 `V5-CM-2.4`(적재 검증) 통과다. 실제 해금은 B가 `V5-CM-1.3` 직후,
   A가 `V5-CM-2.4` 직후, C·D가 `V5-CM-3.2~3.3` 직후다(WBS v5 §8).
5. 공용 DB는 각 단계에서 preflight → rehearse → apply → 재실행 no-op → 검증을 통과해야
   다음 DB로 넘어간다.

`CLAUDE.md`와 `AGENTS.md`는 이 문서를 진입점으로 사용하며 자기 참조 마지막 줄 외 내용이
같아야 한다.
