---
doc_id: DOC-TROUBLE-FDC
title: FDC 이상 유형 진단 및 조치 가이드
doc_type: TROUBLESHOOT
model_code: COMMON
version: Rev.1.5-corrected
---

> 교육용으로 작성된 가상 문서입니다. PHOTO · ETCH 공통 적용.
> 이 정정본은 최종 조치 규칙과 graph/routing 경계를 기준으로 구 조치 표현을 제거한 적재 정본입니다.

# FDC 이상 유형 진단 및 조치 가이드

## 1. 판정 기준

파라미터 값은 다섯 개의 기준선과 비교한다. LSL/USL은 규격한계이고, LCL/UCL은 관리한계다.

| 판정 | 조건 | 의미 |
|---|---|---|
| `IN_CONTROL` | 관리한계 안 | 정상 |
| `OOC` | 관리한계를 벗어났으나 규격한계 안 | 공정 불안정 경고 |
| `OOS` | 규격한계를 벗어남 | 제품 품질 영향 가능성이 있는 조치 대상 |

## 2. 알람 규칙

| RULE | 대상 | 조건 | 판정 |
|---|---|---|---|
| `R01` | raw point (`fdc_trace`) | USL/LSL을 1점이라도 벗어남 | `OOS` TRACE 알람 |
| `R02` | summary mean (`summary_data`) | 관리한계 UCL/LCL을 벗어남 | `OOC` SUMMARY 알람 |
| `R03_CONSEC` | chamber·parameter·recipe step | `chamber_wafer_cum` 순서에서 같은 chamber·parameter·recipe step의 OOS run이 연속 3에 최초 도달 | R03 파생 알람 |

R03는 서로 다른 parameter나 recipe step의 OOS를 합산하지 않는다. LOT 경계를 넘어 같은 chamber·parameter·recipe step의 연속 run을 계산하고, 연속 3에 처음 도달할 때 한 번 발행한다.

## 3. Fault 후보

Agent의 Fault 후보는 `FOC`, `RFM`, `MFD`, `TMD`, `OTH`다. `NRM`은 정상 라벨이며 고장 후보로 출력하지 않는다.

| 후보 | 대표 파라미터 | 설명 |
|---|---|---|
| `FOC` | `PH_FOCUS` | PHOTO Focus Offset 이탈 |
| `RFM` | `ET_REFL` | ETCH RF 정합 불량 |
| `MFD` | `ET_CF4` | ETCH MFC 유량 이탈 |
| `TMD` | `ET_ESC` | ETCH 정전척 온도 이상 |
| `OTH` | 복합·미분류 | 위 네 유형으로 설명하기 어려운 이상 |

## 4. 이상 유형별 확인

### 4.1 FOC — Focus Excursion

Focus Offset 절대값이 커지면 패턴 경계가 흐려지고 CD가 감소할 수 있다. 특정 챔버에서만 발생하면 척 표면 이물이나 포커스 센서 교정 상태를 우선 확인한다.

### 4.2 RFM — RF Mismatch

Reflected Power가 상승하면 플라즈마에 실제로 들어가는 실효 전력이 줄어 식각량이 부족해질 수 있다. 정합기 튜닝, 챔버 내벽 폴리머, 상부 전극 소모를 확인한다.

### 4.3 MFD — MFC Flow Drift

CF4 Flow가 기준 범위를 벗어나면 식각 반응 가스가 부족하거나 과다할 수 있다. MFC 교정 상태와 가스 공급 계통을 확인한다.

### 4.4 TMD — ESC Temperature Deviation

ESC Temperature 이탈은 웨이퍼 위치별 식각 속도 차이를 만들 수 있다. He 가스 누설, 냉각수 유량, 정전척 표면 상태를 확인한다.

## 5. 상류 원인 판별

`CD_AEI` 불량이 있는데 ETCH 파라미터가 정상 범위라면, 원인은 ETCH 장비가 아니라 이전 PHOTO STEP일 수 있다.

판별 경계는 다음과 같다.

1. 그래프는 PROCESS STEP 수준의 `CT-PHOTO → CT-ETCH` 인접 관계를 제공한다.
2. 실제 WAFER가 거친 PHOTO/ETCH 설비는 `lot_history`로 조회한다.
3. B Graph Tool은 특정 LOT routing을 추정하지 않는다.
4. C Agent는 AlarmRef에서 해석한 LOT/WAFER 범위 안에서 routing을 결합한다.

## 6. 조치 결정 기준

조치는 알람 근거에 따라 3단계 deterministic rule로만 결정한다.

| incident 근거 | 조치 | 외부 효과 | 승인 |
|---|---|---|---|
| SUMMARY OOC만 존재 | `MONITORING` | 없음 | 자동 |
| TRACE OOS 존재, strict R03 없음 | `WARNING` | 이메일 | 자동 |
| strict R03 존재 | `EQP_HOLD` | 승인 요청 이메일, 승인 후 Kafka MES Mock | 사람 승인 |

계측 결과, LLM 판단, predicted fault, 보조 이상도, routing 설명은 근거 설명에 사용할 수 있지만 이 3단계 규칙 결과를 바꾸지 못한다.

## 7. EQP_HOLD 승인 경계

`EQP_HOLD`는 설비 투입을 차단하는 강한 조치이므로 사람 승인이 필요하다. 승인 전에는 Kafka MES Mock 이벤트를 발행하지 않는다.

승인 요청 이메일은 담당자에게 판단을 요청하는 알림이다. MES Mock 전송은 승인 후에만 실행한다.

## 8. 조치 권고 작성 시 포함할 항목

| 항목 | 내용 |
|---|---|
| 이상 유형 | `FOC` / `RFM` / `MFD` / `TMD` / `OTH` 중 하나 |
| 근거 파라미터 | 어느 파라미터가 어느 RECIPE STEP에서 벗어났는지 |
| 대상 범위 | 장비 전체인지 특정 챔버인지 |
| 조치 코드 | `MONITORING` / `WARNING` / `EQP_HOLD` |
| 승인 필요 여부 | `EQP_HOLD`만 승인 필요 |
| 참조 근거 | relation_id, document_id, chunk_id 등 재조회 가능한 근거 ID |

이 시스템은 장비를 직접 제어하지 않는다. 권고와 전송까지만 수행하고, 실제 조치는 사람이 승인한 뒤 현장에서 실시한다.
