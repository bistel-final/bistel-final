// Agent 실행 fixture — incident 1건당 run 1건 (총 10건)
// TODO(api): GET /agent/runs/{run_id} 미구현 — API 명세 스키마 그대로의 mock
//
// run_id는 API 명세 형식 RUN-YYYYMMDD-####. 시퀀스는 incident 최초 알람 시각의 전역 시간순이며,
// 지시서 §2가 지정한 "ALM-0019 런 = RUN-20260603-0005"와 일치함을 검증했다.
// incident = LOT + 챔버 + 파라미터 (알람 수 4·6·5·3·6·3·6·5·7·6 · 합계 51 검증)
// confidence 0.87은 Agent 판정값 (anomaly_score 0.84와 다른 지표 — §2)

export const RUNS = [
  {
    "run_id": "RUN-20260601-0001",
    "thread_id": "thread-20260601-0001",
    "action_id": "ACT-0001",
    "status": "COMPLETED",
    "incident": {
      "lot_id": "LOT-260003",
      "chamber_id": "ETC-01-C2",
      "sensor_id": "ET_REFL",
      "recipe_step_name": "MAIN_ETCH",
      "first_at": "2026-06-01 23:17:23",
      "last_at": "2026-06-01 23:36:49",
      "alarm_count": 4
    },
    "alarm_ids": [
      "ALM-0001",
      "ALM-0002",
      "ALM-0003",
      "ALM-0004"
    ],
    "trigger_alarm_id": "ALM-0001",
    "fault_code": "RFM",
    "confidence": 0.87
  },
  {
    "run_id": "RUN-20260602-0002",
    "thread_id": "thread-20260602-0002",
    "action_id": "ACT-0002",
    "status": "COMPLETED",
    "incident": {
      "lot_id": "LOT-260004",
      "chamber_id": "ETC-01-C2",
      "sensor_id": "ET_REFL",
      "recipe_step_name": "MAIN_ETCH",
      "first_at": "2026-06-02 07:20:23",
      "last_at": "2026-06-02 07:40:26",
      "alarm_count": 6
    },
    "alarm_ids": [
      "ALM-0005",
      "ALM-0006",
      "ALM-0007",
      "ALM-0008",
      "ALM-0009",
      "ALM-0010"
    ],
    "trigger_alarm_id": "ALM-0005",
    "fault_code": "RFM",
    "confidence": 0.87
  },
  {
    "run_id": "RUN-20260602-0003",
    "thread_id": "thread-20260602-0003",
    "action_id": "ACT-0003",
    "status": "COMPLETED",
    "incident": {
      "lot_id": "LOT-260005",
      "chamber_id": "ETC-01-C2",
      "sensor_id": "ET_REFL",
      "recipe_step_name": "MAIN_ETCH",
      "first_at": "2026-06-02 15:19:49",
      "last_at": "2026-06-02 15:39:42",
      "alarm_count": 5
    },
    "alarm_ids": [
      "ALM-0011",
      "ALM-0012",
      "ALM-0013",
      "ALM-0014",
      "ALM-0015"
    ],
    "trigger_alarm_id": "ALM-0011",
    "fault_code": "RFM",
    "confidence": 0.87
  },
  {
    "run_id": "RUN-20260602-0004",
    "thread_id": "thread-20260602-0004",
    "action_id": "ACT-0004",
    "status": "COMPLETED",
    "incident": {
      "lot_id": "LOT-260006",
      "chamber_id": "PHO-01-C1",
      "sensor_id": "PH_FOCUS",
      "recipe_step_name": "EXPOSE",
      "first_at": "2026-06-02 22:38:32",
      "last_at": "2026-06-02 22:54:15",
      "alarm_count": 3
    },
    "alarm_ids": [
      "ALM-0016",
      "ALM-0017",
      "ALM-0018"
    ],
    "trigger_alarm_id": "ALM-0016",
    "fault_code": "FOC",
    "confidence": 0.87
  },
  {
    "run_id": "RUN-20260603-0005",
    "thread_id": "thread-20260603-0005",
    "action_id": "ACT-0005",
    "status": "WAITING_APPROVAL",
    "incident": {
      "lot_id": "LOT-260007",
      "chamber_id": "PHO-01-C1",
      "sensor_id": "PH_FOCUS",
      "recipe_step_name": "EXPOSE",
      "first_at": "2026-06-03 06:37:47",
      "last_at": "2026-06-03 06:53:46",
      "alarm_count": 6
    },
    "alarm_ids": [
      "ALM-0019",
      "ALM-0020",
      "ALM-0021",
      "ALM-0022",
      "ALM-0023",
      "ALM-0024"
    ],
    "trigger_alarm_id": "ALM-0019",
    "fault_code": "FOC",
    "confidence": 0.87
  },
  {
    "run_id": "RUN-20260603-0006",
    "thread_id": "thread-20260603-0006",
    "action_id": "ACT-0007",
    "status": "COMPLETED",
    "incident": {
      "lot_id": "LOT-260008",
      "chamber_id": "PHO-01-C1",
      "sensor_id": "PH_FOCUS",
      "recipe_step_name": "EXPOSE",
      "first_at": "2026-06-03 14:39:36",
      "last_at": "2026-06-03 14:54:42",
      "alarm_count": 6
    },
    "alarm_ids": [
      "ALM-0025",
      "ALM-0026",
      "ALM-0027",
      "ALM-0028",
      "ALM-0029",
      "ALM-0030"
    ],
    "trigger_alarm_id": "ALM-0025",
    "fault_code": "FOC",
    "confidence": 0.87
  },
  {
    "run_id": "RUN-20260603-0007",
    "thread_id": "thread-20260603-0007",
    "action_id": "ACT-0006",
    "status": "COMPLETED",
    "incident": {
      "lot_id": "LOT-260008",
      "chamber_id": "ETC-01-C1",
      "sensor_id": "ET_CF4",
      "recipe_step_name": "MAIN_ETCH",
      "first_at": "2026-06-03 15:43:05",
      "last_at": "2026-06-03 15:52:49",
      "alarm_count": 3
    },
    "alarm_ids": [
      "ALM-0031",
      "ALM-0032",
      "ALM-0033"
    ],
    "trigger_alarm_id": "ALM-0031",
    "fault_code": "MFD",
    "confidence": 0.87
  },
  {
    "run_id": "RUN-20260603-0008",
    "thread_id": "thread-20260603-0008",
    "action_id": "ACT-0009",
    "status": "COMPLETED",
    "incident": {
      "lot_id": "LOT-260009",
      "chamber_id": "PHO-01-C1",
      "sensor_id": "PH_FOCUS",
      "recipe_step_name": "EXPOSE",
      "first_at": "2026-06-03 22:26:40",
      "last_at": "2026-06-03 22:42:21",
      "alarm_count": 7
    },
    "alarm_ids": [
      "ALM-0034",
      "ALM-0035",
      "ALM-0036",
      "ALM-0037",
      "ALM-0038",
      "ALM-0039",
      "ALM-0040"
    ],
    "trigger_alarm_id": "ALM-0034",
    "fault_code": "FOC",
    "confidence": 0.87
  },
  {
    "run_id": "RUN-20260603-0009",
    "thread_id": "thread-20260603-0009",
    "action_id": "ACT-0008",
    "status": "COMPLETED",
    "incident": {
      "lot_id": "LOT-260009",
      "chamber_id": "ETC-01-C1",
      "sensor_id": "ET_CF4",
      "recipe_step_name": "MAIN_ETCH",
      "first_at": "2026-06-03 23:12:41",
      "last_at": "2026-06-03 23:31:50",
      "alarm_count": 5
    },
    "alarm_ids": [
      "ALM-0041",
      "ALM-0042",
      "ALM-0043",
      "ALM-0044",
      "ALM-0045"
    ],
    "trigger_alarm_id": "ALM-0041",
    "fault_code": "MFD",
    "confidence": 0.87
  },
  {
    "run_id": "RUN-20260604-0010",
    "thread_id": "thread-20260604-0010",
    "action_id": "ACT-0010",
    "status": "WAITING_APPROVAL",
    "incident": {
      "lot_id": "LOT-260010",
      "chamber_id": "ETC-01-C1",
      "sensor_id": "ET_CF4",
      "recipe_step_name": "MAIN_ETCH",
      "first_at": "2026-06-04 07:10:41",
      "last_at": "2026-06-04 07:29:49",
      "alarm_count": 6
    },
    "alarm_ids": [
      "ALM-0046",
      "ALM-0047",
      "ALM-0048",
      "ALM-0049",
      "ALM-0050",
      "ALM-0051"
    ],
    "trigger_alarm_id": "ALM-0046",
    "fault_code": "MFD",
    "confidence": 0.87
  }
]

// Tool은 4종만 존재하며 이 화면에서 표시하는 것은 3종
// (send_action은 승인 후 전송 단계에서 실행). decide_action·classify_fault는 Tool이 아니라 노드.
export const RUN_TOOL_CALLS = [
  { seq: 1, name: 'get_fdc_summary', status: 'OK', ms: 412 },
  { seq: 2, name: 'get_equipment_context', status: 'OK', ms: 388 },
  { seq: 3, name: 'search_documents', status: 'OK', ms: 1240 },
]

export const RUN_NODES = [
  { seq: 1, name: 'classify_fault' },
  { seq: 2, name: 'decide_action' },
]
