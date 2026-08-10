// 조치 이력 fixture — incident 1건 = 조치 1건 (총 10건)
// TODO(api): GET /actions 목록 미구현 — GET /actions/{action_id} 스키마 기준 mock
//
// created_at 10건은 디자인 v2 조치 목록 시안 값(실측 CSV 대조 완료본).
// ACT-0002 승인 흐름은 감사로그 시안 기준: 요청 07:21:23(=created_at, 동일 트랜잭션) → 승인 07:31:23.
// severity·승인·채널은 도메인 규칙 4 기본 결정표에서 결정된다 (임의 지정 아님).
// 탭 배지 2/0/0/8/10 과 알람 수 합계 51 검증 완료.

export const ACTIONS = [
  {
    "action_id": "ACT-0001",
    "run_id": "RUN-20260601-0001",
    "action_code": "MONITOR",
    "severity": "LOW",
    "channel": "EMAIL",
    "approval_status": "AUTO",
    "send_status": "SENT",
    "approved_by": null,
    "approved_at": null,
    "created_at": "2026-06-01 23:20",
    "incident": {
      "lot_id": "LOT-260003",
      "chamber_id": "ETC-01-C2",
      "sensor_id": "ET_REFL"
    },
    "alarm_ids": [
      "ALM-0001",
      "ALM-0002",
      "ALM-0003",
      "ALM-0004"
    ]
  },
  {
    "action_id": "ACT-0002",
    "run_id": "RUN-20260602-0002",
    "action_code": "EQP_HOLD",
    "severity": "HIGH",
    "channel": "MES",
    "approval_status": "APPROVED",
    "send_status": "SENT",
    "approved_by": "bang",
    "approved_at": "2026-06-02 07:31:23",
    "created_at": "2026-06-02 07:21:23",
    "incident": {
      "lot_id": "LOT-260004",
      "chamber_id": "ETC-01-C2",
      "sensor_id": "ET_REFL"
    },
    "alarm_ids": [
      "ALM-0005",
      "ALM-0006",
      "ALM-0007",
      "ALM-0008",
      "ALM-0009",
      "ALM-0010"
    ]
  },
  {
    "action_id": "ACT-0003",
    "run_id": "RUN-20260602-0003",
    "action_code": "LOT_HOLD",
    "severity": "MEDIUM",
    "channel": "MES",
    "approval_status": "AUTO",
    "send_status": "SENT",
    "approved_by": null,
    "approved_at": null,
    "created_at": "2026-06-02 15:20",
    "incident": {
      "lot_id": "LOT-260005",
      "chamber_id": "ETC-01-C2",
      "sensor_id": "ET_REFL"
    },
    "alarm_ids": [
      "ALM-0011",
      "ALM-0012",
      "ALM-0013",
      "ALM-0014",
      "ALM-0015"
    ]
  },
  {
    "action_id": "ACT-0004",
    "run_id": "RUN-20260602-0004",
    "action_code": "MONITOR",
    "severity": "LOW",
    "channel": "EMAIL",
    "approval_status": "AUTO",
    "send_status": "SENT",
    "approved_by": null,
    "approved_at": null,
    "created_at": "2026-06-02 22:42",
    "incident": {
      "lot_id": "LOT-260006",
      "chamber_id": "PHO-01-C1",
      "sensor_id": "PH_FOCUS"
    },
    "alarm_ids": [
      "ALM-0016",
      "ALM-0017",
      "ALM-0018"
    ]
  },
  {
    "action_id": "ACT-0005",
    "run_id": "RUN-20260603-0005",
    "action_code": "EQP_HOLD",
    "severity": "HIGH",
    "channel": "MES",
    "approval_status": "PENDING",
    "send_status": "WAITING",
    "approved_by": null,
    "approved_at": null,
    "created_at": "2026-06-03 06:39",
    "incident": {
      "lot_id": "LOT-260007",
      "chamber_id": "PHO-01-C1",
      "sensor_id": "PH_FOCUS"
    },
    "alarm_ids": [
      "ALM-0019",
      "ALM-0020",
      "ALM-0021",
      "ALM-0022",
      "ALM-0023",
      "ALM-0024"
    ]
  },
  {
    "action_id": "ACT-0006",
    "run_id": "RUN-20260603-0007",
    "action_code": "MONITOR",
    "severity": "LOW",
    "channel": "EMAIL",
    "approval_status": "AUTO",
    "send_status": "SENT",
    "approved_by": null,
    "approved_at": null,
    "created_at": "2026-06-03 15:45",
    "incident": {
      "lot_id": "LOT-260008",
      "chamber_id": "ETC-01-C1",
      "sensor_id": "ET_CF4"
    },
    "alarm_ids": [
      "ALM-0031",
      "ALM-0032",
      "ALM-0033"
    ]
  },
  {
    "action_id": "ACT-0007",
    "run_id": "RUN-20260603-0006",
    "action_code": "LOT_HOLD",
    "severity": "MEDIUM",
    "channel": "MES",
    "approval_status": "AUTO",
    "send_status": "SENT",
    "approved_by": null,
    "approved_at": null,
    "created_at": "2026-06-03 14:43",
    "incident": {
      "lot_id": "LOT-260008",
      "chamber_id": "PHO-01-C1",
      "sensor_id": "PH_FOCUS"
    },
    "alarm_ids": [
      "ALM-0025",
      "ALM-0026",
      "ALM-0027",
      "ALM-0028",
      "ALM-0029",
      "ALM-0030"
    ]
  },
  {
    "action_id": "ACT-0008",
    "run_id": "RUN-20260603-0009",
    "action_code": "LOT_HOLD",
    "severity": "MEDIUM",
    "channel": "MES",
    "approval_status": "AUTO",
    "send_status": "SENT",
    "approved_by": null,
    "approved_at": null,
    "created_at": "2026-06-03 23:14",
    "incident": {
      "lot_id": "LOT-260009",
      "chamber_id": "ETC-01-C1",
      "sensor_id": "ET_CF4"
    },
    "alarm_ids": [
      "ALM-0041",
      "ALM-0042",
      "ALM-0043",
      "ALM-0044",
      "ALM-0045"
    ]
  },
  {
    "action_id": "ACT-0009",
    "run_id": "RUN-20260603-0008",
    "action_code": "LOT_HOLD",
    "severity": "MEDIUM",
    "channel": "MES",
    "approval_status": "AUTO",
    "send_status": "SENT",
    "approved_by": null,
    "approved_at": null,
    "created_at": "2026-06-03 22:29",
    "incident": {
      "lot_id": "LOT-260009",
      "chamber_id": "PHO-01-C1",
      "sensor_id": "PH_FOCUS"
    },
    "alarm_ids": [
      "ALM-0034",
      "ALM-0035",
      "ALM-0036",
      "ALM-0037",
      "ALM-0038",
      "ALM-0039",
      "ALM-0040"
    ]
  },
  {
    "action_id": "ACT-0010",
    "run_id": "RUN-20260604-0010",
    "action_code": "EQP_HOLD",
    "severity": "HIGH",
    "channel": "MES",
    "approval_status": "PENDING",
    "send_status": "WAITING",
    "approved_by": null,
    "approved_at": null,
    "created_at": "2026-06-04 07:11",
    "incident": {
      "lot_id": "LOT-260010",
      "chamber_id": "ETC-01-C1",
      "sensor_id": "ET_CF4"
    },
    "alarm_ids": [
      "ALM-0046",
      "ALM-0047",
      "ALM-0048",
      "ALM-0049",
      "ALM-0050",
      "ALM-0051"
    ]
  }
]

// 승인 요청 — APR ID는 요청 시각 오름차순 재부여 (디자인 v2 §5-1)
// APR-0001 = ACT-0002(APPROVED) / APR-0002 = ACT-0005(PENDING) / APR-0003 = ACT-0010(PENDING)
export const APPROVALS = [
  {
    "approval_id": "APR-0001",
    "action_id": "ACT-0002",
    "run_id": "RUN-20260602-0002",
    "status": "APPROVED",
    "requested_at": "2026-06-02 07:21:23",
    "decided_by": "bang",
    "decided_at": "2026-06-02 07:31:23"
  },
  {
    "approval_id": "APR-0002",
    "action_id": "ACT-0005",
    "run_id": "RUN-20260603-0005",
    "status": "PENDING",
    "requested_at": "2026-06-03 06:39",
    "decided_by": null,
    "decided_at": null
  },
  {
    "approval_id": "APR-0003",
    "action_id": "ACT-0010",
    "run_id": "RUN-20260604-0010",
    "status": "PENDING",
    "requested_at": "2026-06-04 07:11",
    "decided_by": null,
    "decided_at": null
  }
]

export const ACTION_TABS = [
  { key: 'PENDING', label: '승인 대기' },
  { key: 'FAILED', label: '전송 실패' },
  { key: 'SENDING', label: '진행 중' },
  { key: 'SENT', label: '완료' },
  { key: 'ALL', label: '전체' },
]

// 센서별 Fault 분류 (원인 문구)
export const FAULT_BY_SENSOR = {
  PH_FOCUS: { code: 'FOC', name: '포커스 이탈', name_en: 'Focus Excursion', cause: 'PH_FOCUS 연속 OOS — CD_ADI 불량 유발' },
  ET_REFL: { code: 'RFM', name: 'RF 정합 이상', name_en: 'RF Mismatch', cause: '반사파 상승 = RF 정합 불량, 실효 전력 저하' },
  ET_CF4: { code: 'MFD', name: 'CF4 유량 저하', name_en: 'Mass Flow Drop', cause: 'CF4 유량 저하로 식각 부족' },
}
