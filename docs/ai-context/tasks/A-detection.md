# A — Detection

> 기준 원천: 멘토 최종 `project.zip`(2026-08-18) · epoch `fdc_final_20260818`
> 기준 문서: 요구사항 v2.1 · 시스템설계서 v2.1 · 역할분담 v10.1 · API v3 · WBS v5
> 마지막 동기화: 2026-08-19
> 담당: 신동원 · 모듈 `backend/app/detection/` · `frontend/src/features/detection/`

Trace 집계·evaluation 판정·알람 재현·R03 파생·비지도 score·격리 평가와 화면 1·2를 풀스택으로
책임진다.

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

## Task (WBS v5)

| ID | 내용 | 공수 |
|---|---|---:|
| V5-A-1.1 | Summary 4,800 재계산 | 2.0h |
| V5-A-1.2 | evaluation IN 4,538 / OOC 216 / OOS 46 재현 | 2.0h |
| V5-A-1.3 | TRACE 138 · SUMMARY 51 알람 | 2.0h |
| V5-A-1.4 | R03 3건 파생 | 2.0h |
| V5-A-1.5 | incident 12 집계 | 1.5h |
| V5-A-2.1 | 비지도 anomaly score | 2.5h |
| V5-A-2.2 | score 경계 고정 | 1.0h |
| V5-A-2.3 | 합성 라벨 격리 | 1.5h |
| V5-A-2.4 | Detection 평가 artifact | 2.0h |
| V5-A-3.1 | `GET /alarms` | 2.0h |
| V5-A-3.2 | `GET /trace`·`GET /parameters` | 1.5h |
| V5-A-3.3 | 화면 1 Dashboard | 2.5h |
| V5-A-3.4 | 화면 2 Alarm History | 2.5h |
| V5-A-3.5 | 호환 필드 adapter | 1.0h |
| V5-A-4.1 | Detection 회귀 fixture | 2.0h |

**합계 28.0h**

---

## 완료 기준 (최종 실측값)

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

---

## 주의

**이미 최종 데이터에 반영된 값을 다시 보정하지 않는다.** `dim_parameter` overlay, `seq_no`
correction, Summary·Metrology 시각 보정은 이전 epoch의 결함을 고치던 작업이며 폐기됐다.

**R03는 `chamber_wafer_cum` 오름차순 연속 3으로만 성립한다.** 같은 `(chamber, parameter,
recipe step)` 안에서 LOT 경계를 넘어 계산하고, 비OOS에서 연속 수를 초기화하며, 연속 3에 처음
도달할 때 한 번 발행한다. 서로 다른 parameter·step의 OOS를 합산하지 않는다.

**R03의 WAFER 3개를 AlarmRef 3개로 축약하지 않는다.** `member_wafer_refs` 3개와 그 세 WAFER의
raw OOS point에 해당하는 TRACE `member_alarm_refs` 9개를 별도로 갖는다.

**`upper_only=true`인 parameter는 하한을 판정하지 않는다.** `dim_parameter`의 명시적 메타데이터로
산출하며 값이 null인지로 추론하지 않는다.

**`fault_code`는 평가 loader에서만 읽는다.** Runtime repository와 타입을 분리하고 모델
feature·threshold 선택, Agent 프롬프트·Tool·RAG 입력으로 전달하지 않는다.
후보 model·feature·normalization·threshold는 공개 label join **전에** 고정한다.

**anomaly score는 조치에 관여하지 않는다.** incident 생성·조치 상하향·`EQP_HOLD`·승인 게이트에
사용하지 않으며, score가 없거나 모델이 준비되지 않아도 규칙 처리 결과가 같아야 한다.

**metrology 48/600을 전체 성능으로 외삽하지 않는다.** 평가 결과에는
`label_source=SYNTHETIC_GENERATOR`와 `production_ground_truth_available=false`를 표시한다.

**알람 식별은 `(source, alarm_id)`다.** source 없는 ID만으로 deep link를 만들지 않는다.

---

## API

```http
GET  /alarms          호환 필수. 기간·area 필터, R03 포함 여부는 명시 파라미터
GET  /trace           호환 필수. lot·wafer·chamber·parameter
GET  /parameters      호환 필수. 8개 parameter와 5선
```

선택 확장: dataset bounds, dashboard summary, source-aware alarm detail, paged 목록.
참고 React의 축약 필드는 호환 projection으로 한시 지원하고 canonical field로 교체하는 adapter를
둔다(`V5-A-3.5`).

---

## 화면

| 화면 | 내용 |
|---|---|
| 1 Dashboard | KPI·추이·상위 parameter·설비별 건수 |
| 2 Alarm History | 목록·필터·상세, source-aware deep link |

Loading / Error / Empty / Success를 구분 표시한다.

---

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
