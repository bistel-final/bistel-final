# A — Detection

> 기준 요구사항: v1.9 / 시스템설계서: v1.10 / 역할분담: v9.6
> 마지막 동기화: 2026-08-11
> 담당: 신동원 · 모듈 `backend/app/detection/` · `frontend/src/features/detection/`

FDC 요약 재계산, 규칙 판정, 이상감지 모델, 운영 대시보드·알람·trace 화면까지 풀스택으로 책임진다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-A-01 | 요약 재계산 | 필수 |
| FR-A-02 | 규칙 판정 R01·R02·R03 | 필수 |
| FR-A-03 | IsolationForest 학습·저장 | 필수 |
| FR-A-04 | anomaly_score 산출 | 필수 |
| FR-A-05 | Tool `get_fdc_summary` | 필수 |
| FR-A-06 | 알람·대시보드 API | 필수 |
| FR-A-07 | 알람·대시보드 화면 | 필수 |
| FR-A-08 | *(결번 — FR-C-12로 이동)* | — |
| FR-A-09 | 모델 고도화 | 도전 |

---

## 완료 기준 (수용 기준 요약)

```
요약 재계산   PK(lot_hist_id, sensor_id, recipe_step_no) 1,600건 일치
              point_cnt·ooc_point_cnt·oos_point_cnt·judgement 완전 일치
              숫자형 허용 오차 0.0001 이하
규칙 판정     R01 34 / R02 14 / R03 3, 전체 51건 재현
              R03는 ALM-0008·0022·0048과 발생 위치 일치
모델          ML 실행 전후 fdc_alarm 51건 불변
              lot_id 기준 분리, random_state 고정, P·R·F1 (0.80은 비강제 목표)
Tool          잘못된 lot_hist_id에도 ok:false, 예외 미발생
              lot_hist_id당 anomaly_score 정확히 1개
대시보드      기간 미지정 시 2026-06-01~06-04 · reference_date 2026-06-04
              알람 51 · OOS 37 · OOC 14
              일별 추이·설비별 건수 합계가 필터된 알람 수와 일치
              파라미터 TOP5·최근 5건의 결정론적 정렬, 승인 대기 누락 없음
```

---

## 주의

**요약·판정 재현은 비파괴로 한다.** 공용 기준 DB에 알람을 다시 INSERT하지 않는다.
`backend/scripts/verify_detection_rules.py`가 메모리·임시 스키마에서 후보를 만들고
canonical key `(lot_hist_id, chamber_id, sensor_id, recipe_step_no, rule_id, judgement, hit_cnt, occurred_at)`로
원본 fixture와 순서 무관 집합 비교한다. (설계 5.1.1)

**R03는 상태 테이블 없이 전체 정렬 재계산**으로 구현한다. 연속 카운터 테이블을 두지 않는다. (설계 5.2)

**ET_REFL은 상한만 판정**한다. LSL=LCL=0이다. Trace의 `upper_only=true`는 하한값 null 여부가 아니라 Backend의 명시적 센서 메타데이터 규칙으로 산출한다.

**anomaly_score는 `predict()`가 아니라 정규화 점수 ≥ 0.62**로 `is_anomaly`를 정한다. C와 축을 맞추기 위함이다. (설계 5.3)

**`lot_history.fault_code`는 평가 정답으로만** 쓴다. feature 생성·Agent 입력에서 제외한다.

**ML은 `fdc_alarm`을 변경하지 않는다.** ML 기반 알람 생성은 범위 밖이다.

**승인 대기 수를 직접 세지 않는다.** C의 `GET /approvals` 또는 공유 `ApprovalService.count_pending()`을 호출한다. (요구사항 11.1)

**KPI에 LLM을 쓰지 않는다.** SQL·규칙으로 계산한다.

---

## API

```http
GET  /dashboard/summary          date_from?, date_to?, area?, equipment_id?, chamber_id?
GET  /summaries/{lot_hist_id}
GET  /alarms                     date_from?, date_to?, area?, equipment_id?, chamber_id?,
                                 sensor_id?, rule_id?, judgement?, page, size
GET  /alarms/{alarm_id}
GET  /traces/catalog             조회 선택지만. 시계열 없음
POST /traces/search              sensor_ids[] · wafer_nos[] · 기간을 함께 받으므로 POST
```

DTO는 설계 10.2를 그대로 따른다. 필터 복수 지정은 AND다.

대시보드는 **파라미터·추이 중심**이다. 챔버 상태 카드와 계측 패스율은 응답에서 빠졌고 `daily_trend`·`top_sensors`·`equipment_counts`가 대신 들어간다.

---

## 화면

| 경로 | 내용 |
|---|---|
| `/dashboard` | 알람 대시보드 — 기간·AREA·설비 필터, 알람 추이, 상위 파라미터, 설비별 건수, 승인 대기 목록 |
| `/alarms`, `/alarms/:alarmId` | 알람 목록·상세 (C의 Agent 결과 연결) |
| `/traces` | 파라미터 다중 선택 + LOT·WAFER 다중 조회, 선택 개수만큼 차트 |

최초 진입은 `date_from`·`date_to`를 생략하고 API가 반환한 `date_range`·`reference_date`를 표시한다. 한쪽 경계만 주면 서버가 나머지를 데이터 경계로 보완하며, 사용자가 기간을 고른 뒤 계층 필터를 바꿔도 선택 기간을 유지한다. (FR-A-07)

Loading·Error·Empty를 구분 표시한다. (NFR-17)

---

## 원본 절

```
설계 5.1  요약 재계산
설계 5.1.1  R01·R02 판정과 51건 비파괴 재현
설계 5.2  R03_CONSEC 결정론적 판정
설계 5.3  IsolationForest 입력과 단일 anomaly score
설계 5.4  get_fdc_summary Tool
설계 5.5  대시보드 조회 설계
설계 10.2  A Detection API DTO
요구사항 5.1  FR-A-01~09 + 대시보드 KPI 확정 규칙
요구사항 8.1  알람 판정 규칙
```
