# 02. FDC 도메인 규칙과 불변 수치

> [!CAUTION]
> **사용 중지 — 아래 본문은 이전 epoch·부분 동기화 이력이며 구현 근거로 사용하면 안 됩니다.**
> 현재는 `docs/ai-context/README.md`에서 안내하는 최종 패키지 기준표와 v2.1 요구사항·설계·
> 역할분담·API v3만 사용합니다. WBS v5와 이 요약문이 함께 재생성되기 전에는 아래 본문의
> 참고·복사·프롬프트 입력을 금지합니다.

> 기준 요구사항: v1.9
> 기준 시스템설계서: v1.10
> 기준 역할분담: v9.6
> 마지막 동기화: 2026-08-11

---

## 1. 불변 수치 (배포 데이터 실측값)

아래 값은 교육생 배포패키지 CSV로 재계산해 검증한 값이다.
**추측하거나 다시 계산해서 다른 값을 쓰지 않는다.** 대부분 그대로 테스트 수용 기준이다.

```
fdc_alarm            51   R01_OOS 34 / R02_OOC 14 / R03_CONSEC 3
                          judgement  OOS 37 / OOC 14
R03_CONSEC 알람           ALM-0008 · ALM-0022 · ALM-0048
incident (lot_id, chamber_id)  10개  →  agent_run 10건 · action_history 10건

fdc_summary       1,600   IN_CONTROL 1,552 / OOC 14 / OOS 34
fdc_trace         4,800
lot_history         200   fault_code  NRM 150 / FOC 20 / RFM 15 / MFD 15 / TMD 0
metrology            40   PASS 34 / FAIL 6
action_history       10   AUTO 7 / PENDING 2 / APPROVED 1

알람 51건에 결합한 fault_code   FOC 22 / RFM 15 / MFD 14 / NRM 0
Neo4j                 노드 24 · 관계 26
document 3 · document_chunk 39   (ET-7500 13 · PH-9000 12 · TROUBLE 14)
임베딩 BAAI/bge-m3 1024차원, NULL 0건
```

**대시보드 기본 적용 기간** (기간 경계 미지정 시 선택 계층의 전체 데이터 경계)

```
date_range  2026-06-01 ~ 2026-06-04 · reference_date 2026-06-04
알람 51 · OOS 37 · OOC 14
일별 추이 합계·설비별 건수 합계 = 필터된 알람 수
파라미터 TOP5  alarm_count DESC, sensor_id ASC
최근 알람 5건  occurred_at DESC, alarm_id DESC
```

**골든 시나리오**

```
1. 자동조치   ALM-0001  LOT-260003 / ETC-01-C2 / R02_OOC → MONITOR / EMAIL
2. 승인       ALM-0022  LOT-260007 / PHO-01-C1 / R03_CONSEC → EQP_HOLD / MES
3. 반려       ALM-0048  LOT-260010 / ETC-01-C1 / R03_CONSEC → EQP_HOLD
4. 장애 처리  Tool 실패(4-A) · n8n 전송 실패(4-B)
   복합 이상   ALM-0031  LOT-260008 / ETC-01-C1 / ET_CF4 R02_OOC → MONITOR
```

근거: 요구사항 7.2·부록 B / 설계 5.5·14.1

---

## 2. 판정 단위

```
AREA > EQUIPMENT > CHAMBER          PHOTO·ETCH 2구역, 설비 2대, 챔버 4개
PROCESS STEP                        CT-PHOTO → CT-ETCH
RECIPE STEP                         EXPOSE·DEVELOP / MAIN_ETCH·OVER_ETCH  ← FDC 판정 단위
SENSOR 8종                          한계선 5개  LSL < LCL < TARGET < UCL < USL
```

- **알람은 WAFER 단위**로 발생한다.
- **조치는 incident 단위**다. incident key = `(lot_id, chamber_id)`.
- 요약은 `(lot_hist_id, sensor_id, recipe_step_no)` 단위다.

---

## 3. 알람 규칙 (A)

| 규칙 | 조건 | 판정 |
|---|---|---|
| R01_OOS | `oos_point_cnt >= 1` | OOS |
| R02_OOC | `oos_point_cnt = 0 AND ooc_point_cnt >= 2` | OOC |
| R03_CONSEC | 연속 3 WAFER OOS | OOS(챔버 이상) |

**R03_CONSEC 확정 규칙** — 이 조합만이 배포 3건을 위치까지 재현한다.

```
판별 키   (chamber_id, sensor_id, recipe_step_no)
정렬      chamber_wafer_cum ASC, track_in_at ASC, lot_hist_id ASC
LOT 경계  초기화하지 않고 넘어서 계산
재무장    OOS가 아닌 판정에서 연속 수 0으로 초기화
발행      연속 수가 2 → 3이 되는 시점에 1회만. 4장 이상에서 추가 발행 없음
```

**예외**: `ET_REFL`은 LSL=LCL=0이므로 **상한만 판정**한다 (UCL 21.0 / USL 30.0). Trace의 `upper_only=true`는 하한값 null 여부가 아니라 Backend의 명시적 센서 메타데이터 규칙으로 산출하며 원본 스키마를 바꾸지 않는다.

근거: 요구사항 8.1 / 설계 5.1.1·5.2

---

## 4. 조치 결정 규칙 (C)

`decide_action`은 **순수 함수**다. DB·LLM에 접근하지 않는다.

### 적용 순서

```
① 순수 연쇄 이상 → 조치 미생성 (recommended_action=NULL, action_history 미생성)
② R03_CONSEC     → EQP_HOLD
③ 기본 결정표
④ 상향 조건 → 한 단계 상향, 최대 LOT_HOLD (여러 조건이 겹쳐도 한 번만)
⑤ 상향 없고 단발 후 정상 복귀 → MONITOR
```

### 기본 결정표 (동일 incident 범위에서 계수)

| 조건 | 조치 | 승인 | severity | 채널 |
|---|---|---|---|---|
| R03_CONSEC 발생 | EQP_HOLD | **HITL** | HIGH | MES |
| DISTINCT wafer_no 기준 OOS 3장 이상 | LOT_HOLD | 자동 | MEDIUM | MES |
| OOS 1~2장 | NOTIFY | 자동 | MEDIUM | EMAIL |
| OOC만 발생 | MONITOR | 자동 | LOW | EMAIL |

> OOS는 **알람 건수가 아니라 `COUNT(DISTINCT wafer_no)`** 로 센다.
> 한 WAFER가 복수 RECIPE STEP에서 OOS면 알람은 여러 건이지만 WAFER는 1장이다.

### 상향 조건 (③ 이후, 최대 LOT_HOLD)

| 조건 | 판정 기준 |
|---|---|
| CD_AEI 직접 연결 | `alarm.lot_id = metrology.lot_id` AND `alarm.wafer_no = metrology.wafer_no` AND `measure_type='CD_AEI'` AND `judgement='FAIL'` |
| 반복 LOT | 챔버상 **바로 이전 처리 LOT**과 현재 LOT에 동일 `(chamber_id, sensor_id, recipe_step_no, rule_id)` 알람. 중간에 다른 LOT이 있으면 거짓 |
| 하류 전파 | 동일 LOT·WAFER의 NEXT_STEP 하류 `lot_history.track_in_at <= alarm.occurred_at` |

**EQP_HOLD는 R03_CONSEC 등 챔버 수준 근거로만 만든다.** 상향으로는 도달하지 못한다.
`anomaly_score`(`SEVERITY_HIGH_THRESHOLD=0.80`)는 보조 신호이며 단독으로 EQP_HOLD를 만들지 않는다.

### 하향 조건

- **순수 연쇄 이상**: ETCH 센서 4종이 모두 IN_CONTROL(OOC/OOS 0건) + 같은 WAFER PHOTO OOS → 하류 조치 생성 안 함
- **단발 후 정상 복귀**: 기본이 NOTIFY이고 상향 조건이 전혀 없으며, 챔버상 **바로 다음 실제 처리 WAFER**의 동일 판별 키 요약이 IN_CONTROL → MONITOR
  - OOS 고유 WAFER가 2장 이상이면 단발이 아니다
  - 동일 키 요약이 없으면 이후 관측까지 **건너뛰어 찾지 않는다**

근거: 요구사항 8.2 / 설계 7.7

---

## 5. 조치 이력 생성 시점

조치가 확정되면 **전송·승인 여부와 관계없이 `action_history` 1건을 즉시 생성**한다.

```
자동 조치   approval_status=AUTO,    send_status=WAITING, approved_by='system', approved_at=created_at
EQP_HOLD    approval_status=PENDING, send_status=WAITING, approved_by=NULL,     approved_at=NULL
            + approval_request(status=PENDING, action_id 포함)를 같은 트랜잭션에서 생성
```

승인·반려 시 **새 행을 만들지 않고 기존 행을 갱신**한다.

| 경로 | approval_request | action_history | send_status |
|---|---|---|---|
| 승인 | PENDING → APPROVED | PENDING → APPROVED (`approved_by=decided_by`) | WAITING → SENDING → SENT |
| 반려 | PENDING → REJECTED | PENDING → REJECTED (승인자 NULL 유지) | WAITING → CANCELED |
| 자동 | (미생성) | AUTO | WAITING → SENDING → SENT |
| 생략 | (미생성) | (행 미생성) | — |

근거: 요구사항 8.3 / 설계 7.5

---

## 6. Fault Code

```
FOC  Focus Excursion      포커스 이탈       주 센서 PH_FOCUS
RFM  RF Mismatch          RF 정합 불량      주 센서 ET_REFL
MFD  MFC Flow Drift       가스 유량 이탈    주 센서 ET_CF4
TMD  ESC Temperature      척 온도 이상      주 센서 ET_ESC  ← 배포 표본 0건
NRM  정상
```

`lot_history.fault_code`는 **모델 학습·평가 정답으로만** 쓴다.
분류 입력·프롬프트·Tool 결과에 포함하지 않는다 (데이터 누수 방지).

TMD는 표본이 없으므로 정량 평가에서 제외하고 TROUBLE 3.4 기반 합성 fixture로 검증한다.

근거: 요구사항 FR-A-03·FR-C-15 / 설계 7.6

---

## 7. 용어

| 용어 | 정의 |
|---|---|
| OOS | Out of Spec. 규격한계(LSL/USL) 이탈. 이상 확정 |
| OOC | Out of Control. 규격은 안쪽이나 관리한계(LCL/UCL) 이탈. 경고 |
| IN_CONTROL | DB 판정값 3종 중 정상. 문서의 "IN_SPEC"은 **OOC/OOS 0건**을 뜻한다 |
| incident | 조치 단위. `(lot_id, chamber_id)` |
| HITL | EQP_HOLD를 사람 승인 후에만 전송하는 구조 |

상세 용어는 배포 패키지 `00_용어집.pdf`.
