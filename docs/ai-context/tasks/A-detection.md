# A — Detection

> 기준 원천: 멘토님 제공 최종 `project.zip`(2026-08-18) · epoch `fdc_final_20260818`
> 기준 문서: 요구사항 v2.1 · 시스템설계서 v2.1 · 역할분담 v10.1 · API v3 · WBS v5
> 마지막 동기화: 2026-08-20
> 담당: 신동원 · 모듈 `backend/app/detection/` · `frontend/src/features/detection/`

Trace 집계·evaluation 판정·알람 재현·R03 파생·비지도 score·격리 평가와 화면 1·2를 풀스택으로
책임진다. Task ID·완료 기준·선행관계·공수는 WBS v5와 1:1로 유지한다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-A-01 | Summary 재계산 | 필수 |
| FR-A-02 | evaluation·알람 재현 | 필수 |
| FR-A-03 | R03 파생 | 필수 |
| FR-A-04 | 비지도 anomaly score | 필수 |
| FR-A-05 | score 근거 제공 | 필수 |
| FR-A-06 | 알람·Trace·대시보드 API | 필수 |
| FR-A-07 | 화면 연동 | 필수 |
| FR-A-08 | 합성 라벨 격리 평가 | 필수 |
| FR-A-09 | 모델 고도화 | 도전 |

관련 비기능 요구사항은 NFR-06(테스트), NFR-09(Tool 공통 계약), NFR-11·13(API 계약·시간),
NFR-17(UI 상태), NFR-19(합성 GT 비누수)다.

## Task (WBS v5 정본)

| ID | P | 완료 기준 | FR/NFR | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-A-1.1 | P0 | Summary 재계산. 완료: Trace에서 `summary_data` 4,800건을 결정론적으로 재현하고 불일치 0건을 확인한다 | FR-A-01 | V5-CM-2.6 | 2.0h |
| V5-A-1.2 | P0 | evaluation 재현. 완료: point 판정으로 IN 4,538 / OOC 216 / OOS 46을 재현한다. `upper_only` parameter의 하한 미판정을 포함한다 | FR-A-02 | V5-A-1.1 | 2.0h |
| V5-A-1.3 | P0 | TRACE·SUMMARY 알람. 완료: TRACE 138·SUMMARY 51을 재현하고 저장 알람 합계 189를 확인한다. 시각 NULL 0건이다 | FR-A-02 | V5-A-1.2 | 2.0h |
| V5-A-1.4 | P0 | R03 파생·적재. 완료: 같은 chamber·parameter·recipe step에서 `chamber_wafer_cum` 오름차순 연속 3 최초 도달로 R03 3건을 결정론적으로 만들고 CM-3.1의 3개 DB 빈 table에 멱등 적재한다. 각 R03는 member wafer 3개와 TRACE AlarmRef 9개를 가지며 View는 저장 알람 189·R03 포함 192·AlarmRef 중복 0을 반환한다 | FR-A-03, FR-A-06 | V5-A-1.3, V5-CM-3.1 | 2.0h |
| V5-A-1.5 | P0 | incident 집계. 완료: 알람이 있는 `(lot_id, chamber_id)` 12개를 산출하고 참고 action 12건과 1:1임을 확인한다. R03 포함 합계 192를 검증한다 | FR-A-03, FR-A-06 | V5-A-1.4 | 1.5h |
| V5-A-2.1 | P0 | 비지도 anomaly score. 완료: LOT 단위 분리로 재현 가능한 score를 만들고 feature·seed·normalization·model version을 고정하며 Fault·metrology·Generator 누수 0건을 검증한다 | FR-A-04, NFR-08, NFR-19 | V5-A-1.5 | 2.0h |
| V5-A-2.2 | P0 | score 경계 고정. 완료: score가 조치·incident·승인 게이트에 전달되지 않음을 계약 테스트로 고정한다. score 없이도 규칙 처리가 동일하다 | FR-A-05 | V5-A-2.1 | 1.0h |
| V5-A-2.3 | P0 | 합성 라벨 격리. 완료: `fault_code`를 평가 loader에서만 읽고 Runtime repository 타입과 분리한다. 모델 feature·threshold·Tool·API에 사용하지 않음을 allowlist·query·payload 테스트로 고정한다 | FR-A-08, NFR-19 | V5-A-2.1 | 1.5h |
| V5-A-2.4 | P1 | Detection 평가 artifact·holdout. 완료: 공개 라벨을 읽기 전에 후보 model·feature·threshold와 prediction hash를 고정한 뒤 격리 synthetic holdout을 1회 평가하고 같은 revision 재튜닝을 금지한다. metrology 48/600, 합성 label metadata와 운영 성능 비주장을 기록한다 | FR-A-08, FR-A-09, NFR-19 | V5-A-2.3 | 2.0h |
| V5-A-3.1 | P0 | `GET /alarms`. 완료: 최종 필터·`(source, alarm_id)`·189/192 계약, 안정 정렬과 offset 포함 시간을 제공한다 | FR-A-06, NFR-11, NFR-13 | V5-A-1.5, V5-CM-4.1 | 2.0h |
| V5-A-3.2 | P0 | `GET /trace`·`GET /parameters`. 완료: 참고 React 호환 응답과 canonical field를 단일 boundary projection으로 제공하고 안정 정렬·빈 배열 계약을 지킨다 | FR-A-06, NFR-11, NFR-13 | V5-A-3.1 | 1.5h |
| V5-A-3.2-1 | P0 | `get_fdc_summary(lot_hist_id)` Tool. 완료: 단일 `lot_hist_id`로 summary·evaluation·5선과 준비된 경우에만 nullable score provenance를 반환하고 Fault GT·action 권고를 제외한다. 모델 artifact가 없어도 성공하며 성공·실패·0건·timeout은 공통 `ok`·`reason`·빈 payload 계약과 공통 reason prefix를 따른다 | FR-A-05, NFR-09, NFR-19 | V5-A-1.5, V5-CM-4.1 | 1.5h |
| V5-A-3.3 | P1 | 화면 1 Dashboard. 완료: KPI·추이·상위 parameter를 실제 API로 연결하고 Loading·Error·Empty·Success를 component test로 구분한다 | FR-A-07, FR-I-02, NFR-17 | V5-A-3.2 | 2.0h |
| V5-A-3.4 | P1 | 화면 2 Alarm History. 완료: 목록·필터·상세와 source-aware deep link를 실제 API에 연결한다. 분석 실행 버튼은 선택 AlarmRef를 `POST /agent/runs`에 보내고 202·409·422·503 상태와 생성 run deep-link를 처리한다. Loading·Error·Empty·Success를 검증한다 | FR-A-07, FR-I-02~03, NFR-17 | V5-A-3.3, V5-C-5.1 | 2.0h |
| V5-A-3.5 | P1 | 호환 필드 adapter. 완료: 참고 React 축약 필드를 한시 지원하고 canonical field로 교체하는 경로를 남긴다 | FR-I-03 | V5-A-3.2 | 1.0h |
| V5-A-4.1 | P1 | Detection 회귀. 완료: 재계산·알람·R03·incident·Tool·label non-leakage를 fixture로 고정하고 CI에서 재현한다 | NFR-06, NFR-09, NFR-19 | V5-A-2.4, V5-A-3.2-1 | 2.0h |

**합계 16 Task / 28.0h**

---

## 완료 기준과 불변식

```text
Summary 재계산   summary_data 4,800건 결정론적 재현 · 불일치 0
evaluation       IN 4,538 / OOC 216 / OOS 46          (합 4,800)
저장 알람        TRACE 138 · SUMMARY 51 · 합계 189 · 시각 NULL 0
R03 파생         3건 · 각 member wafer 3 · TRACE AlarmRef 9
                 R03 명시 포함 합계 192
incident         알람이 있는 (lot_id, chamber_id) 12개
                 참고 action fixture 12건과 1:1 (MONITORING 5 / WARNING 4 / EQP_HOLD 3)
dim_parameter    8행 · 5선 · upper_only 포함
fdc_trace        14,400 · seq_no 0..5 · 선언 PK 중복 0
metrology        48 · PASS 39 / FAIL 9 · coverage 48/600 병기
```

- `upper_only=true`인 parameter는 명시 메타데이터로 하한을 판정하지 않는다.
- 알람 식별자는 항상 `(source, alarm_id)`이며 source 없는 ID로 deep link를 만들지 않는다.
- anomaly score는 설명용 nullable 근거다. incident·조치·승인 게이트에는 사용하지 않는다.
- `fault_code`는 격리 평가 loader만 읽는다. Runtime repository·feature·threshold·Tool·API·Agent
  입력에는 노출하지 않는다.
- 후보 model·feature·normalization·threshold와 prediction hash는 공개 합성 라벨을 읽기 전에
  고정한다. 합성 holdout은 같은 revision에서 1회만 평가하며 재튜닝하지 않는다.
- 평가 artifact에는 `label_source=SYNTHETIC_GENERATOR`,
  `production_ground_truth_available=false`, metrology coverage 48/600을 기록한다.

## API·Tool 계약

```http
GET  /alarms
GET  /trace
GET  /parameters
```

`get_fdc_summary(lot_hist_id)`는 단일 `lot_hist_id`를 받아 summary·evaluation·5선과, 준비된
경우에만 nullable score provenance를 반환한다. 모델 artifact가 없어도 성공한다. Fault GT와
action 권고는 반환하지 않는다. API와 Tool 모두 canonical DTO,
offset 포함 시각, 안정 정렬, 빈 배열·빈 payload 계약을 따른다. 참고 React 축약 필드는
`V5-A-3.5` boundary adapter에서만 한시 지원한다.

## 선행조건·협업 주의

- 데이터 재현은 `V5-CM-2.6`, API·Tool 공통 계약은 `V5-CM-4.1` 이후 착수한다.
- C의 Level 1·2(`V5-C-2.1`)는 `V5-A-3.2-1` 완료 전 통합 완료로 볼 수 없다.
- 화면 2의 분석 실행 버튼은 C의 `V5-C-5.1` 실행 시작 API 이후 연결한다.
- `get_fdc_summary`가 성공해도 Fault 정답이나 조치 결정을 C에 전달하지 않는다.
- 화면 1·2는 Loading·Error·Empty·Success 네 상태를 component test로 구분한다.
- 이전 epoch의 overlay·`seq_no` 보정 로직을 최종 데이터에 재적용하지 않는다.

## 원본 절

```text
요구사항 v2.1  5.1 FR-A-01~09
설계 v2.1      2.6 공개 합성 라벨 격리
               4.1 Summary 재계산 · 4.2 evaluation·TRACE alarm · 4.3 SUMMARY alarm
               4.4 anomaly score · 4.5 공개 합성 라벨 평가
               3.2 R03 reference · 3.5 통합 View
역할분담 v10.1  6. A — Detection Full-stack
기준표          2. 검증된 물리 데이터 · 3. Fault 라벨과 평가 경계
```
