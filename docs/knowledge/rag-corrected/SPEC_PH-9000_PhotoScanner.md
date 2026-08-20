---
doc_id: DOC-SPEC-PH9000
title: PH-9000 Photo Scanner 장비 스펙 및 운전 기준
doc_type: SPEC
model_code: PH-9000
version: Rev.2.1-corrected
---

> 교육용으로 작성된 가상 장비 문서입니다. 실제 제품 규격이 아닙니다.
> 이 정정본은 최종 `project.zip`의 RAG 원문을 기준으로 하되, 최종 데이터·그래프 계약과 충돌하는 고정 설비 상하류 표현을 제거한 적재 정본입니다.

# PH-9000 Photo Scanner 장비 스펙 및 운전 기준

## 1. 적용 범위

PH-9000은 PHOTO AREA에서 사용하는 노광 장비 모델이다. 최종 데이터 기준에서 PH-9000 모델은 `EQP01`, `EQP02`, `EQP03`에 적용된다. 각 설비는 `PM1`, `PM2` 챔버를 갖는다.

담당 PROCESS STEP은 `CT-PHOTO`이며, 적용 RECIPE는 `RECIPE01`, `RECIPE03`이다. 문서에 등장하는 개별 설비 ID는 모델 설명을 위한 예시일 뿐 특정 ETCH 설비와 고정 연결된 pair가 아니다.

## 2. 장비 개요

PH-9000은 웨이퍼에 감광액을 바르고 마스크 패턴을 전사한 뒤 현상까지 수행한다. Contact 층 패턴 크기인 CD가 이 STEP에서 처음 결정된다.

PHOTO STEP에서 발생한 편차는 이후 `CT-ETCH` 결과에 영향을 줄 수 있다. 다만 상하류는 설비 ID 사이가 아니라 PROCESS STEP 수준의 `CT-PHOTO → CT-ETCH` 흐름으로 해석한다.

특정 WAFER가 실제로 어느 PHOTO 설비와 어느 ETCH 설비를 거쳤는지는 `lot_history`의 LOT/WAFER routing으로 조회해야 한다.

## 3. RECIPE 구성

| RECIPE STEP | 하는 일 | 이상이 드러나는 항목 |
|---|---|---|
| `EXPOSE` | 마스크를 통해 빛을 쬐어 패턴을 새긴다 | Exposure Dose, Focus Offset |
| `DEVELOP` | 현상액으로 빛을 받은 부분을 녹여 패턴을 드러낸다 | Developer Temperature |

FDC 판정은 RECIPE STEP 단위로 수행한다. 같은 파라미터라도 단계에 따라 의미가 달라지므로, EXPOSE 구간 이탈과 DEVELOP 구간 이탈은 원인을 분리해 확인한다.

## 4. 파라미터 운전 기준

| PARAMETER | 이름 | 단위 | LSL | LCL | TARGET | UCL | USL |
|---|---|---|---|---|---|---|---|
| `PH_DOSE` | Exposure Dose | mJ/cm2 | 24.0 | 24.4 | 25.0 | 25.6 | 26.0 |
| `PH_FOCUS` | Focus Offset | nm | -60.0 | -36.0 | 0.0 | 36.0 | 60.0 |
| `PH_PEB` | PEB Temperature | degC | 113.0 | 113.8 | 115.0 | 116.2 | 117.0 |
| `PH_DEV` | Developer Temperature | degC | 22.4 | 22.64 | 23.0 | 23.36 | 23.6 |

LSL/USL은 규격한계이고, LCL/UCL은 관리한계다. 규격한계를 벗어난 raw point는 R01 기준 TRACE 알람으로 기록된다.

## 5. 주요 이상 유형

### 5.1 Focus Offset (`PH_FOCUS`)

초점이 맞는 면에서 벗어난 정도이다. 초점이 벗어나면 마스크 패턴이 선명하게 전사되지 않아 패턴 경계가 흐려지고, 현상 후 CD가 목표보다 작게 형성될 수 있다.

주요 원인은 웨이퍼 척 표면 이물, 웨이퍼 평탄도 편차, 포커스 센서 교정 이탈이다. 이 항목의 대표 Fault 후보는 `FOC`다.

### 5.2 Exposure Dose (`PH_DOSE`)

감광액에 주는 빛 에너지이다. 노광량이 부족하면 패턴이 두껍게 남고, 과다하면 지나치게 녹아 패턴이 얇아질 수 있다.

광원 출력 저하나 조명 경로 오염이 주요 원인이다. 단발 OOC는 관찰 대상이며, OOS 및 R03 여부는 공통 조치 규칙에 따라 판단한다.

## 6. 하류 영향 해석

ETCH 계측 불량이 관측되더라도 PH-9000과 특정 ETCH 설비를 문서만으로 직접 연결하지 않는다. 올바른 해석 순서는 다음과 같다.

1. 문제가 된 WAFER의 `lot_history`를 조회한다.
2. `CT-PHOTO` 행에서 실제 PHOTO 설비와 챔버를 확인한다.
3. `CT-ETCH` 행에서 실제 ETCH 설비와 챔버를 확인한다.
4. 그래프의 PROCESS STEP 인접 관계는 PHOTO 다음이 ETCH라는 구조 근거로만 사용한다.

따라서 PH-9000 문서는 PHOTO 모델의 파라미터·원인·점검 기준을 제공하고, 실제 상류 설비 식별은 routing 조회 결과에 따른다.

## 7. 챔버 비교 확인

| 관측 | 판단 |
|---|---|
| 한 챔버 WAFER만 이탈 | 해당 챔버 고유 문제 가능성 |
| 두 챔버 모두 이탈 | 장비 공통 문제 가능성 |

챔버 문제와 장비 공통 문제를 구분하면 점검 범위를 줄일 수 있다. 조치 코드는 공통 3단계 규칙인 `MONITORING`, `WARNING`, `EQP_HOLD` 중에서 알람 근거에 따라 결정한다.
