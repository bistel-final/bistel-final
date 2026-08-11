<<<<<<< Updated upstream
// Agent 실행 fixture — 명세 AgentRunDetailResponse 스키마
// TODO(api): 실서버 연결 시 이 파일만 교체.
//
// incident = (lot_id, chamber_id) 두 개뿐이다 (명세 IncidentRef).
// sensor_id·equipment_id·recipe_step_name·incident_first_at/last_at·alarm_count 는 형제 필드다.
// thread_id 는 agent_run_id 와 같은 값. tool_calls.status 는 'SUCCESS' (OK 아님).
// decide_action·classify_fault 는 Tool 이 아니라 노드이므로 nodes 로 분리한다.
=======
// Agent 실행 fixture — incident 1건당 run 1건 (총 10건)
// TODO(api): GET /agent/runs · GET /agent/runs/{run_id} 미구현 — API 명세 스키마 그대로의 mock
//
// agent_run_id는 API 명세 형식 RUN-YYYYMMDD-####. 시퀀스는 incident 최초 알람 시각의 전역 시간순이며,
// 지시서 §2가 지정한 "ALM-0019 런 = RUN-20260603-0005"와 일치함을 검증했다.
// incident = LOT + 챔버 + 파라미터 (알람 수 4·6·5·3·6·3·6·5·7·6 · 합계 51 검증)
// confidence 0.87은 Agent 판정값 (anomaly_score 0.84와 다른 지표 — §2)
>>>>>>> Stashed changes

export const RUNS = [
  {
    "agent_run_id": "RUN-20260601-0001",
<<<<<<< Updated upstream
    "thread_id": "RUN-20260601-0001",
    "requested_alarm_id": "ALM-0001",
    "representative_alarm_id": "ALM-0001",
=======
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
>>>>>>> Stashed changes
    "alarm_ids": [
      "ALM-0001",
      "ALM-0002",
      "ALM-0003",
      "ALM-0004"
    ],
    "alarm_count": 4,
    "incident": {
      "lot_id": "LOT-260003",
      "chamber_id": "ETC-01-C2"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_step_name": "MAIN_ETCH",
    "incident_first_at": "2026-06-01 23:17:23",
    "incident_last_at": "2026-06-01 23:36:49",
    "status": "COMPLETED",
    "fault_code": "RFM",
    "fault_name": "RF 정합 이상",
    "fault_name_en": "RF Mismatch",
    "cause_summary": "반사파 상승 — RF 정합 불량으로 실효 전력 저하",
    "recommended_action": "MONITOR",
    "action_reason": "OOC만 발생",
    "severity": "LOW",
    "approval_required": false,
    "confidence": 0.87,
    "action_id": "ACT-0001",
    "llm_model": "kosa-api",
    "latency_ms": 9310,
    "started_at": "2026-06-01 23:17:23",
    "ended_at": "2026-06-01 23:20",
    "tool_calls": [
      {
        "tool_call_id": "RUN-20260601-0001-T1",
        "call_seq": 1,
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "latency_ms": 412,
        "called_at": "2026-06-01 23:17:23",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260601-0001-T2",
        "call_seq": 2,
        "tool_name": "get_equipment_context",
        "status": "SUCCESS",
        "latency_ms": 388,
        "called_at": "2026-06-01 23:17:23",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260601-0001-T3",
        "call_seq": 3,
        "tool_name": "search_documents",
        "status": "SUCCESS",
        "latency_ms": 1240,
        "called_at": "2026-06-01 23:17:23",
        "error_msg": null
      }
    ],
    "nodes": [
      {
        "node_seq": 1,
        "node_name": "classify_fault"
      },
      {
        "node_seq": 2,
        "node_name": "decide_action"
      }
    ]
  },
  {
    "agent_run_id": "RUN-20260602-0002",
<<<<<<< Updated upstream
    "thread_id": "RUN-20260602-0002",
    "requested_alarm_id": "ALM-0005",
    "representative_alarm_id": "ALM-0005",
=======
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
>>>>>>> Stashed changes
    "alarm_ids": [
      "ALM-0005",
      "ALM-0006",
      "ALM-0007",
      "ALM-0008",
      "ALM-0009",
      "ALM-0010"
    ],
    "alarm_count": 6,
    "incident": {
      "lot_id": "LOT-260004",
      "chamber_id": "ETC-01-C2"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_step_name": "MAIN_ETCH",
    "incident_first_at": "2026-06-02 07:20:23",
    "incident_last_at": "2026-06-02 07:40:26",
    "status": "COMPLETED",
    "fault_code": "RFM",
    "fault_name": "RF 정합 이상",
    "fault_name_en": "RF Mismatch",
    "cause_summary": "반사파 상승 — RF 정합 불량으로 실효 전력 저하",
    "recommended_action": "EQP_HOLD",
    "action_reason": "R03_CONSEC — 연속 3 WAFER OOS",
    "severity": "HIGH",
    "approval_required": true,
    "confidence": 0.87,
    "action_id": "ACT-0002",
    "llm_model": "kosa-api",
    "latency_ms": 9310,
    "started_at": "2026-06-02 07:20:23",
    "ended_at": "2026-06-02 07:21:23",
    "tool_calls": [
      {
        "tool_call_id": "RUN-20260602-0002-T1",
        "call_seq": 1,
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "latency_ms": 412,
        "called_at": "2026-06-02 07:20:23",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260602-0002-T2",
        "call_seq": 2,
        "tool_name": "get_equipment_context",
        "status": "SUCCESS",
        "latency_ms": 388,
        "called_at": "2026-06-02 07:20:23",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260602-0002-T3",
        "call_seq": 3,
        "tool_name": "search_documents",
        "status": "SUCCESS",
        "latency_ms": 1240,
        "called_at": "2026-06-02 07:20:23",
        "error_msg": null
      }
    ],
    "nodes": [
      {
        "node_seq": 1,
        "node_name": "classify_fault"
      },
      {
        "node_seq": 2,
        "node_name": "decide_action"
      }
    ]
  },
  {
    "agent_run_id": "RUN-20260602-0003",
<<<<<<< Updated upstream
    "thread_id": "RUN-20260602-0003",
    "requested_alarm_id": "ALM-0011",
    "representative_alarm_id": "ALM-0011",
=======
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
>>>>>>> Stashed changes
    "alarm_ids": [
      "ALM-0011",
      "ALM-0012",
      "ALM-0013",
      "ALM-0014",
      "ALM-0015"
    ],
    "alarm_count": 5,
    "incident": {
      "lot_id": "LOT-260005",
      "chamber_id": "ETC-01-C2"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_step_name": "MAIN_ETCH",
    "incident_first_at": "2026-06-02 15:19:49",
    "incident_last_at": "2026-06-02 15:39:42",
    "status": "COMPLETED",
    "fault_code": "RFM",
    "fault_name": "RF 정합 이상",
    "fault_name_en": "RF Mismatch",
    "cause_summary": "반사파 상승 — RF 정합 불량으로 실효 전력 저하",
    "recommended_action": "LOT_HOLD",
    "action_reason": "DISTINCT wafer_no 기준 OOS 3장 이상",
    "severity": "MEDIUM",
    "approval_required": false,
    "confidence": 0.87,
    "action_id": "ACT-0003",
    "llm_model": "kosa-api",
    "latency_ms": 9310,
    "started_at": "2026-06-02 15:19:49",
    "ended_at": "2026-06-02 15:20",
    "tool_calls": [
      {
        "tool_call_id": "RUN-20260602-0003-T1",
        "call_seq": 1,
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "latency_ms": 412,
        "called_at": "2026-06-02 15:19:49",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260602-0003-T2",
        "call_seq": 2,
        "tool_name": "get_equipment_context",
        "status": "SUCCESS",
        "latency_ms": 388,
        "called_at": "2026-06-02 15:19:49",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260602-0003-T3",
        "call_seq": 3,
        "tool_name": "search_documents",
        "status": "SUCCESS",
        "latency_ms": 1240,
        "called_at": "2026-06-02 15:19:49",
        "error_msg": null
      }
    ],
    "nodes": [
      {
        "node_seq": 1,
        "node_name": "classify_fault"
      },
      {
        "node_seq": 2,
        "node_name": "decide_action"
      }
    ]
  },
  {
    "agent_run_id": "RUN-20260602-0004",
<<<<<<< Updated upstream
    "thread_id": "RUN-20260602-0004",
    "requested_alarm_id": "ALM-0016",
    "representative_alarm_id": "ALM-0016",
=======
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
>>>>>>> Stashed changes
    "alarm_ids": [
      "ALM-0016",
      "ALM-0017",
      "ALM-0018"
    ],
    "alarm_count": 3,
    "incident": {
      "lot_id": "LOT-260006",
      "chamber_id": "PHO-01-C1"
    },
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_step_name": "EXPOSE",
    "incident_first_at": "2026-06-02 22:38:32",
    "incident_last_at": "2026-06-02 22:54:15",
    "status": "COMPLETED",
    "fault_code": "FOC",
    "fault_name": "포커스 이탈",
    "fault_name_en": "Focus Excursion",
    "cause_summary": "PH_FOCUS 연속 OOS — CD_ADI 불량 유발",
    "recommended_action": "MONITOR",
    "action_reason": "OOC만 발생",
    "severity": "LOW",
    "approval_required": false,
    "confidence": 0.87,
    "action_id": "ACT-0004",
    "llm_model": "kosa-api",
    "latency_ms": 9310,
    "started_at": "2026-06-02 22:38:32",
    "ended_at": "2026-06-02 22:42",
    "tool_calls": [
      {
        "tool_call_id": "RUN-20260602-0004-T1",
        "call_seq": 1,
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "latency_ms": 412,
        "called_at": "2026-06-02 22:38:32",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260602-0004-T2",
        "call_seq": 2,
        "tool_name": "get_equipment_context",
        "status": "SUCCESS",
        "latency_ms": 388,
        "called_at": "2026-06-02 22:38:32",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260602-0004-T3",
        "call_seq": 3,
        "tool_name": "search_documents",
        "status": "SUCCESS",
        "latency_ms": 1240,
        "called_at": "2026-06-02 22:38:32",
        "error_msg": null
      }
    ],
    "nodes": [
      {
        "node_seq": 1,
        "node_name": "classify_fault"
      },
      {
        "node_seq": 2,
        "node_name": "decide_action"
      }
    ]
  },
  {
    "agent_run_id": "RUN-20260603-0005",
<<<<<<< Updated upstream
    "thread_id": "RUN-20260603-0005",
    "requested_alarm_id": "ALM-0019",
    "representative_alarm_id": "ALM-0019",
=======
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
>>>>>>> Stashed changes
    "alarm_ids": [
      "ALM-0019",
      "ALM-0020",
      "ALM-0021",
      "ALM-0022",
      "ALM-0023",
      "ALM-0024"
    ],
    "alarm_count": 6,
    "incident": {
      "lot_id": "LOT-260007",
      "chamber_id": "PHO-01-C1"
    },
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_step_name": "EXPOSE",
    "incident_first_at": "2026-06-03 06:37:47",
    "incident_last_at": "2026-06-03 06:53:46",
    "status": "WAITING_APPROVAL",
    "fault_code": "FOC",
    "fault_name": "포커스 이탈",
    "fault_name_en": "Focus Excursion",
    "cause_summary": "PH_FOCUS 연속 OOS — CD_ADI 불량 유발",
    "recommended_action": "EQP_HOLD",
    "action_reason": "R03_CONSEC — 연속 3 WAFER OOS",
    "severity": "HIGH",
    "approval_required": true,
    "confidence": 0.87,
    "action_id": "ACT-0005",
    "llm_model": "kosa-api",
    "latency_ms": 9310,
    "started_at": "2026-06-03 06:37:47",
    "ended_at": null,
    "tool_calls": [
      {
        "tool_call_id": "RUN-20260603-0005-T1",
        "call_seq": 1,
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "latency_ms": 412,
        "called_at": "2026-06-03 06:37:47",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260603-0005-T2",
        "call_seq": 2,
        "tool_name": "get_equipment_context",
        "status": "SUCCESS",
        "latency_ms": 388,
        "called_at": "2026-06-03 06:37:47",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260603-0005-T3",
        "call_seq": 3,
        "tool_name": "search_documents",
        "status": "SUCCESS",
        "latency_ms": 1240,
        "called_at": "2026-06-03 06:37:47",
        "error_msg": null
      }
    ],
    "nodes": [
      {
        "node_seq": 1,
        "node_name": "classify_fault"
      },
      {
        "node_seq": 2,
        "node_name": "decide_action"
      }
    ]
  },
  {
    "agent_run_id": "RUN-20260603-0006",
<<<<<<< Updated upstream
    "thread_id": "RUN-20260603-0006",
    "requested_alarm_id": "ALM-0025",
    "representative_alarm_id": "ALM-0025",
=======
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
>>>>>>> Stashed changes
    "alarm_ids": [
      "ALM-0025",
      "ALM-0026",
      "ALM-0027",
      "ALM-0028",
      "ALM-0029",
      "ALM-0030"
    ],
<<<<<<< Updated upstream
    "alarm_count": 6,
=======
    "trigger_alarm_id": "ALM-0025",
    "fault_code": "FOC",
    "confidence": 0.87
  },
  {
    "agent_run_id": "RUN-20260603-0007",
    "thread_id": "thread-20260603-0007",
    "action_id": "ACT-0006",
    "status": "COMPLETED",
>>>>>>> Stashed changes
    "incident": {
      "lot_id": "LOT-260008",
      "chamber_id": "PHO-01-C1"
    },
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_step_name": "EXPOSE",
    "incident_first_at": "2026-06-03 14:39:36",
    "incident_last_at": "2026-06-03 14:54:42",
    "status": "COMPLETED",
    "fault_code": "FOC",
    "fault_name": "포커스 이탈",
    "fault_name_en": "Focus Excursion",
    "cause_summary": "PH_FOCUS 연속 OOS — CD_ADI 불량 유발",
    "recommended_action": "LOT_HOLD",
    "action_reason": "DISTINCT wafer_no 기준 OOS 3장 이상",
    "severity": "MEDIUM",
    "approval_required": false,
    "confidence": 0.87,
    "action_id": "ACT-0007",
    "llm_model": "kosa-api",
    "latency_ms": 9310,
    "started_at": "2026-06-03 14:39:36",
    "ended_at": "2026-06-03 14:43",
    "tool_calls": [
      {
        "tool_call_id": "RUN-20260603-0006-T1",
        "call_seq": 1,
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "latency_ms": 412,
        "called_at": "2026-06-03 14:39:36",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260603-0006-T2",
        "call_seq": 2,
        "tool_name": "get_equipment_context",
        "status": "SUCCESS",
        "latency_ms": 388,
        "called_at": "2026-06-03 14:39:36",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260603-0006-T3",
        "call_seq": 3,
        "tool_name": "search_documents",
        "status": "SUCCESS",
        "latency_ms": 1240,
        "called_at": "2026-06-03 14:39:36",
        "error_msg": null
      }
    ],
    "nodes": [
      {
        "node_seq": 1,
        "node_name": "classify_fault"
      },
      {
        "node_seq": 2,
        "node_name": "decide_action"
      }
    ]
  },
  {
    "agent_run_id": "RUN-20260603-0007",
    "thread_id": "RUN-20260603-0007",
    "requested_alarm_id": "ALM-0031",
    "representative_alarm_id": "ALM-0031",
    "alarm_ids": [
      "ALM-0031",
      "ALM-0032",
      "ALM-0033"
    ],
    "alarm_count": 3,
    "incident": {
      "lot_id": "LOT-260008",
      "chamber_id": "ETC-01-C1"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_step_name": "MAIN_ETCH",
    "incident_first_at": "2026-06-03 15:43:05",
    "incident_last_at": "2026-06-03 15:52:49",
    "status": "COMPLETED",
    "fault_code": "MFD",
    "fault_name": "CF4 유량 저하",
    "fault_name_en": "Mass Flow Drop",
    "cause_summary": "CF4 유량 저하로 식각 부족",
    "recommended_action": "MONITOR",
    "action_reason": "OOC만 발생",
    "severity": "LOW",
    "approval_required": false,
    "confidence": 0.87,
    "action_id": "ACT-0006",
    "llm_model": "kosa-api",
    "latency_ms": 9310,
    "started_at": "2026-06-03 15:43:05",
    "ended_at": "2026-06-03 15:45",
    "tool_calls": [
      {
        "tool_call_id": "RUN-20260603-0007-T1",
        "call_seq": 1,
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "latency_ms": 412,
        "called_at": "2026-06-03 15:43:05",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260603-0007-T2",
        "call_seq": 2,
        "tool_name": "get_equipment_context",
        "status": "SUCCESS",
        "latency_ms": 388,
        "called_at": "2026-06-03 15:43:05",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260603-0007-T3",
        "call_seq": 3,
        "tool_name": "search_documents",
        "status": "SUCCESS",
        "latency_ms": 1240,
        "called_at": "2026-06-03 15:43:05",
        "error_msg": null
      }
    ],
    "nodes": [
      {
        "node_seq": 1,
        "node_name": "classify_fault"
      },
      {
        "node_seq": 2,
        "node_name": "decide_action"
      }
    ]
  },
  {
    "agent_run_id": "RUN-20260603-0008",
<<<<<<< Updated upstream
    "thread_id": "RUN-20260603-0008",
    "requested_alarm_id": "ALM-0034",
    "representative_alarm_id": "ALM-0034",
=======
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
>>>>>>> Stashed changes
    "alarm_ids": [
      "ALM-0034",
      "ALM-0035",
      "ALM-0036",
      "ALM-0037",
      "ALM-0038",
      "ALM-0039",
      "ALM-0040"
    ],
<<<<<<< Updated upstream
    "alarm_count": 7,
=======
    "trigger_alarm_id": "ALM-0034",
    "fault_code": "FOC",
    "confidence": 0.87
  },
  {
    "agent_run_id": "RUN-20260603-0009",
    "thread_id": "thread-20260603-0009",
    "action_id": "ACT-0008",
    "status": "COMPLETED",
>>>>>>> Stashed changes
    "incident": {
      "lot_id": "LOT-260009",
      "chamber_id": "PHO-01-C1"
    },
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_step_name": "EXPOSE",
    "incident_first_at": "2026-06-03 22:26:40",
    "incident_last_at": "2026-06-03 22:42:21",
    "status": "COMPLETED",
    "fault_code": "FOC",
    "fault_name": "포커스 이탈",
    "fault_name_en": "Focus Excursion",
    "cause_summary": "PH_FOCUS 연속 OOS — CD_ADI 불량 유발",
    "recommended_action": "LOT_HOLD",
    "action_reason": "DISTINCT wafer_no 기준 OOS 3장 이상",
    "severity": "MEDIUM",
    "approval_required": false,
    "confidence": 0.87,
    "action_id": "ACT-0009",
    "llm_model": "kosa-api",
    "latency_ms": 9310,
    "started_at": "2026-06-03 22:26:40",
    "ended_at": "2026-06-03 22:29",
    "tool_calls": [
      {
        "tool_call_id": "RUN-20260603-0008-T1",
        "call_seq": 1,
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "latency_ms": 412,
        "called_at": "2026-06-03 22:26:40",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260603-0008-T2",
        "call_seq": 2,
        "tool_name": "get_equipment_context",
        "status": "SUCCESS",
        "latency_ms": 388,
        "called_at": "2026-06-03 22:26:40",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260603-0008-T3",
        "call_seq": 3,
        "tool_name": "search_documents",
        "status": "SUCCESS",
        "latency_ms": 1240,
        "called_at": "2026-06-03 22:26:40",
        "error_msg": null
      }
    ],
    "nodes": [
      {
        "node_seq": 1,
        "node_name": "classify_fault"
      },
      {
        "node_seq": 2,
        "node_name": "decide_action"
      }
    ]
  },
  {
    "agent_run_id": "RUN-20260603-0009",
    "thread_id": "RUN-20260603-0009",
    "requested_alarm_id": "ALM-0041",
    "representative_alarm_id": "ALM-0041",
    "alarm_ids": [
      "ALM-0041",
      "ALM-0042",
      "ALM-0043",
      "ALM-0044",
      "ALM-0045"
    ],
    "alarm_count": 5,
    "incident": {
      "lot_id": "LOT-260009",
      "chamber_id": "ETC-01-C1"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_step_name": "MAIN_ETCH",
    "incident_first_at": "2026-06-03 23:12:41",
    "incident_last_at": "2026-06-03 23:31:50",
    "status": "COMPLETED",
    "fault_code": "MFD",
    "fault_name": "CF4 유량 저하",
    "fault_name_en": "Mass Flow Drop",
    "cause_summary": "CF4 유량 저하로 식각 부족",
    "recommended_action": "LOT_HOLD",
    "action_reason": "DISTINCT wafer_no 기준 OOS 3장 이상",
    "severity": "MEDIUM",
    "approval_required": false,
    "confidence": 0.87,
    "action_id": "ACT-0008",
    "llm_model": "kosa-api",
    "latency_ms": 9310,
    "started_at": "2026-06-03 23:12:41",
    "ended_at": "2026-06-03 23:14",
    "tool_calls": [
      {
        "tool_call_id": "RUN-20260603-0009-T1",
        "call_seq": 1,
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "latency_ms": 412,
        "called_at": "2026-06-03 23:12:41",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260603-0009-T2",
        "call_seq": 2,
        "tool_name": "get_equipment_context",
        "status": "SUCCESS",
        "latency_ms": 388,
        "called_at": "2026-06-03 23:12:41",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260603-0009-T3",
        "call_seq": 3,
        "tool_name": "search_documents",
        "status": "SUCCESS",
        "latency_ms": 1240,
        "called_at": "2026-06-03 23:12:41",
        "error_msg": null
      }
    ],
    "nodes": [
      {
        "node_seq": 1,
        "node_name": "classify_fault"
      },
      {
        "node_seq": 2,
        "node_name": "decide_action"
      }
    ]
  },
  {
    "agent_run_id": "RUN-20260604-0010",
<<<<<<< Updated upstream
    "thread_id": "RUN-20260604-0010",
    "requested_alarm_id": "ALM-0046",
    "representative_alarm_id": "ALM-0046",
=======
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
>>>>>>> Stashed changes
    "alarm_ids": [
      "ALM-0046",
      "ALM-0047",
      "ALM-0048",
      "ALM-0049",
      "ALM-0050",
      "ALM-0051"
    ],
    "alarm_count": 6,
    "incident": {
      "lot_id": "LOT-260010",
      "chamber_id": "ETC-01-C1"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_step_name": "MAIN_ETCH",
    "incident_first_at": "2026-06-04 07:10:41",
    "incident_last_at": "2026-06-04 07:29:49",
    "status": "WAITING_APPROVAL",
    "fault_code": "MFD",
    "fault_name": "CF4 유량 저하",
    "fault_name_en": "Mass Flow Drop",
    "cause_summary": "CF4 유량 저하로 식각 부족",
    "recommended_action": "EQP_HOLD",
    "action_reason": "R03_CONSEC — 연속 3 WAFER OOS",
    "severity": "HIGH",
    "approval_required": true,
    "confidence": 0.87,
    "action_id": "ACT-0010",
    "llm_model": "kosa-api",
    "latency_ms": 9310,
    "started_at": "2026-06-04 07:10:41",
    "ended_at": null,
    "tool_calls": [
      {
        "tool_call_id": "RUN-20260604-0010-T1",
        "call_seq": 1,
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "latency_ms": 412,
        "called_at": "2026-06-04 07:10:41",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260604-0010-T2",
        "call_seq": 2,
        "tool_name": "get_equipment_context",
        "status": "SUCCESS",
        "latency_ms": 388,
        "called_at": "2026-06-04 07:10:41",
        "error_msg": null
      },
      {
        "tool_call_id": "RUN-20260604-0010-T3",
        "call_seq": 3,
        "tool_name": "search_documents",
        "status": "SUCCESS",
        "latency_ms": 1240,
        "called_at": "2026-06-04 07:10:41",
        "error_msg": null
      }
    ],
    "nodes": [
      {
        "node_seq": 1,
        "node_name": "classify_fault"
      },
      {
        "node_seq": 2,
        "node_name": "decide_action"
      }
    ]
  }
]
<<<<<<< Updated upstream
=======

// Tool은 4종만 존재하며 이 화면에서 표시하는 것은 3종
// (send_action은 승인 후 전송 단계에서 실행). decide_action·classify_fault는 Tool이 아니라 노드.
export const RUN_TOOL_CALLS = [
  { call_seq: 1, tool_name: 'get_fdc_summary', status: 'SUCCESS', latency_ms: 412 },
  { call_seq: 2, tool_name: 'get_equipment_context', status: 'SUCCESS', latency_ms: 388 },
  { call_seq: 3, tool_name: 'search_documents', status: 'SUCCESS', latency_ms: 1240 },
]
>>>>>>> Stashed changes
