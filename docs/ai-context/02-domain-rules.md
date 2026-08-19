# 02. FDC 도메인 규칙과 불변 수치

> 기준 원천: 멘토 최종 패키지 (2026-08-18) — sample/data/ · 05_검토질문_답변 · 02_화면별_API_가이드
> 보조 기준: 요구사항 v2.0 · 시스템설계서 v2.0 (최종 패키지와 어긋나면 패키지가 우선)
> 마지막 동기화: 2026-08-18

---

## 1. 불변 수치 (최종 데이터 실측값, 8/18 전수 검증)

아래 값은 최종 패키지 `sample/data/` CSV 를 실측해 검증한 값이다.
**추측하거나 다시 계산해서 다른 값을 쓰지 않는다.** 테스트 수용 기준으로 그대로 쓴다.

```
lot_history          600   12 LOT × 25 wafer × 2 step (CT-PHOTO → CT-ETCH)
                           LOT 일자 2026-08-01 ~ 08-12 (하루 1 LOT)
fault_code 분포            NRM 554 / FOC 15 / MFD 13 / RFM 12 / OTH 4 / TMD 2
                           (OTH = 고장모드 매핑 불가 시 기타 — 6장 참조)

summary_alarm_history 51
trace_alarm_history  138
summary_data       4,800   (lot_hist × parameter × step_seq)
evaluation         4,800
fdc_trace         14,400
metrology             48   PASS 39 / FAIL 9  (measured_at = 해당 wafer track_out_at)
action_history        10   EQP_HOLD 3 / WARNING 2 / MONITORING 5
                           — 시드(예시)다. 실제 운영은 Agent 가 런타임에 생성한다
dim_parameter          8   한계선 5선 LSL < LCL < TARGET < UCL < USL
                           upper_only=true 파라미터는 상한(UCL/USL)만 판정

설비 6 (EQP01~06) · 챔버 12 (설비당 PM1·PM2) · AREA 2
incident (lot_id, chamber_id) 알람 조합 = Agent 실행 단위 (현 데이터 10건)
```

**구본(kosa_0813) 대비 값 체계 변경** — 구본 기준 코드·쿼리·기대값은 전부 무효다.

```
area              photo / etch  →  Photo / Etch        (WHERE area='photo' 는 0건이 된다)
summary·evaluation.wafer   번호(1)  →  wafer_id 문자열(LOT001W001)
recipe            RECIPE01~02  →  RECIPE01~04 (area 당 2종, 한 챔버가 시간에 따라 교대 처리)
알람 수           summary 47→51 · trace 126→138  (trace 원시값 보정의 파급)
fault_code        전부 NRM  →  정답 라벨 복원
metrology.measured_at · summary_alarm.occurred_at   공란  →  채워짐
```

근거: sample/data/ 실측 (8/18) · 05_답변 C절

---

## 2. 판정 단위와 계층

```
AREA > EQUIPMENT > CHAMBER          Photo·Etch 2구역 / 설비 6대 / 챔버 12개
PROCESS STEP                        CT-PHOTO → CT-ETCH (ProcessStep NEXT_STEP)
RECIPE STEP                         FDC 판정 단위
PARAMETER 8종                       한계선 5선. upper_only 는 dim_parameter 컬럼이 정의
```

- **알람은 WAFER 단위**로 발생한다 (trace 는 포인트, summary 는 wafer·step 요약 기준).
- **조치는 incident 단위**다. incident key = `(lot_id, chamber_id)`.
- 설비 간 상하류 고정 관계는 **없다**. 공정 흐름은 ProcessStep 의 NEXT_STEP,
  실제 라우팅은 lot_history 가 근거다 (Neo4j 는 마스터/구조만).

근거: 패키지 00_README 데이터 구조 · 05_답변 Q3·RAG 수정 사항

---

## 3. 알람 규칙

알람 산출·재현 절차의 정본은 **패키지 `04_알람_재현_가이드.md`** 다.
여기서는 05_답변으로 확정된 것만 적는다.

**R03 (연속 OOS 챔버 이상) 확정 규칙**

```
판별 키   (chamber_id, parameter, recipe step)
정렬      chamber_wafer_cum 순 (챔버가 실제 처리한 wafer 순서)
LOT 경계  초기화하지 않고 넘어서 연속 계산
발행      연속 3 wafer OOS 도달 시점에 1회
```

근거: 05_답변 Q2

---

## 4. 조치 결정 규칙 — 3단계

조치 어휘는 **MONITORING / WARNING / EQP_HOLD 3단계뿐이다.**
구 어휘 LOT_HOLD · NOTIFY · MONITOR 는 폐어이며 코드·문서·프롬프트에서 쓰지 않는다.

| 조치 | 조건 (incident 범위) | 통지 | 승인 | MES |
|---|---|---|---|---|
| `MONITORING` | OOC 만 발생 | 없음 (관찰) | 자동 | — |
| `WARNING` | OOS 1~2 | 이메일 (n8n 실 SMTP) | 자동 | — |
| `EQP_HOLD` | OOS 3+ = R03 도달 | 이메일 (승인 요청) | **사람 승인 (HITL)** | 승인 후 Kafka `fdc.actions` 발행 |

- `decide_action` 은 **순수 함수·규칙 기반**이다. LLM 판단을 넣지 않는다.
- `anomaly_score` 는 조치 규칙에 직접 반영하지 않는다. Agent 의 보조 근거로만 쓴다.
- EQP_HOLD 는 승인 전에는 MES 로 나가지 않는다. 승인 흐름:
  이메일 승인 요청 → 사람 결정 → 승인 시 MES 이벤트 발행 / 반려 시 미발행.

근거: 05_답변 Q4·Q6 · 02_API 가이드 화면 3

---

## 5. 조치 이력

- `action_history` 는 조치 확정 시 즉시 1건 생성한다. 시드 10건과 동일한 구조로
  Agent 가 런타임에 추가한다.
- `approval_required='Y'` 는 EQP_HOLD 뿐이다. 승인·반려는 기존 행 갱신이며
  새 행을 만들지 않는다.
- 감사 추적은 `action_history` 의 이벤트 시각 컬럼(`approved_at` · `notify_at` ·
  `mes_at` 등)으로 파생한다. (`GET /audit-logs` 담당은 C·D 협의 중 — README 참조)

근거: 02_API 가이드 화면 3 서버 로직

---

## 6. Fault Code (평가 정답 라벨)

```
FOC  Focus Excursion      포커스 이탈       15건
RFM  RF Mismatch          RF 정합 불량      12건
MFD  MFC Flow Drift       가스 유량 이탈    13건
TMD  ESC Temperature      척 온도 이상       2건
OTH  기타(매핑 불가)       대표 parameter 가  4건 — 02_API 가이드 화면 3 정의.
     고장모드에 매핑되지 않는 경우              평가 클래스 포함 여부만 확인
NRM  정상                                  554건
```

`lot_history.fault_code` 는 **평가(채점) 전용**이다.
Agent 판단 입력 · 프롬프트 · Tool 결과 · Text2SQL 응답 가공에 포함하지 않는다 (데이터 누수 방지).

평가 방법 확정: **분류 성능 = fault_code 정답 대조 · 탐지 성능 = metrology.alarm_result(PASS/FAIL) 대조.**

근거: 05_답변 Q5·C절 · sample/data 실측

---

## 7. 차트 규칙 (대시보드·분석 공통)

```
일자별 알람 추이        라인차트 (잠정 확정)
챔버별 알람            누적막대 — OOS/OOC 구분
파라미터별 알람         누적막대 — OOS/OOC 구분
trace 상세             X=시간 · recipe step 경계선 · 한계선 5선 점선 ·
                       알람 포인트만 색 마킹 (OOS 빨강 · OOC 주황)
```

> ★ 일자별 유형은 05_답변("라인차트로 변경")과 02_API 가이드 표기("stack")가 상충한다.
> 05 가 의도적 변경을 명시하므로 **라인을 채택**하되, 멘토 확인으로 최종 확정한다.

근거: 05_답변 Q7 · 02_API 가이드 화면 1·2

---

## 8. 용어

| 용어 | 정의 |
|---|---|
| OOS | Out of Spec. 규격한계(LSL/USL) 이탈. 이상 확정 |
| OOC | Out of Control. 규격 안쪽이나 관리한계(LCL/UCL) 이탈. 경고 |
| incident | 조치·Agent 실행 단위. `(lot_id, chamber_id)` 알람 조합 |
| HITL | EQP_HOLD 를 사람 승인 후에만 전송하는 구조 |
| **금지 용어** | `sensor`(→ 파라미터) · `judgement`(→ alarm_type) · `SPC` |

상세 용어는 패키지 `01_용어집.md`.
