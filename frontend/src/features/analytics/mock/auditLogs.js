// dc.html 감사로그 mock — 시각은 alarms-data.js 실측 occurred_at + offset으로 도출된 값
// (예: RUN-0011 계열 = ALM-0046 07:10:41 + 2/8/11초)
const diff = (k, before, after) => ({ key: k, before, after })

export const AUDIT_EVENT_TYPES = [
  'DETECTION_COMPLETED',
  'AGENT_RUN_STARTED',
  'CLASSIFICATION_COMPLETED',
  'APPROVAL_REQUESTED',
  'APPROVAL_DECIDED',
  'ACTION_SENT',
  'ACTION_SEND_FAILED',
  'AGENT_RUN_COMPLETED',
  'AGENT_RUN_FAILED',
]

export const AUDIT_LOGS = [
  { ts: '2026-06-04 07:29:49', actor: 'SYSTEM', ac: 'SYSTEM', ev: 'DETECTION_COMPLETED', entType: 'fdc_alarm', entId: 'ALM-0051', summary: 'R01_OOS OOS 감지 (ET_CF4)' },
  { ts: '2026-06-04 07:25:02', actor: 'SYSTEM', ac: 'SYSTEM', ev: 'DETECTION_COMPLETED', entType: 'fdc_alarm', entId: 'ALM-0050', summary: 'R01_OOS OOS 감지 (ET_CF4)' },
  { ts: '2026-06-04 07:20:18', actor: 'SYSTEM', ac: 'SYSTEM', ev: 'DETECTION_COMPLETED', entType: 'fdc_alarm', entId: 'ALM-0049', summary: 'R01_OOS OOS 감지 (ET_CF4)' },
  { ts: '2026-06-04 07:15:24', actor: 'SYSTEM', ac: 'SYSTEM', ev: 'DETECTION_COMPLETED', entType: 'fdc_alarm', entId: 'ALM-0048', summary: 'R03_CONSEC OOS 감지 (ET_CF4)' },
  { ts: '2026-06-04 07:15:24', actor: 'SYSTEM', ac: 'SYSTEM', ev: 'DETECTION_COMPLETED', entType: 'fdc_alarm', entId: 'ALM-0047', summary: 'R01_OOS OOS 감지 (ET_CF4)' },
  { ts: '2026-06-04 07:10:43', actor: 'fdc-agent', ac: 'AGENT', ev: 'AGENT_RUN_STARTED', entType: 'agent_run', entId: 'RUN-0011', summary: 'ALM-0046 분석 시작' },
  { ts: '2026-06-04 07:10:49', actor: 'fdc-agent', ac: 'AGENT', ev: 'CLASSIFICATION_COMPLETED', entType: 'agent_run', entId: 'RUN-0011', summary: 'Fault Code MFD 분류' },
  { ts: '2026-06-04 07:10:52', actor: 'fdc-agent', ac: 'AGENT', ev: 'APPROVAL_REQUESTED', entType: 'approval_request', entId: 'APR-0002', summary: 'EQP_HOLD 승인 요청', diff: diff('status', 'null', 'PENDING') },
  { ts: '2026-06-03 22:26:42', actor: 'fdc-agent', ac: 'AGENT', ev: 'AGENT_RUN_STARTED', entType: 'agent_run', entId: 'RUN-0009', summary: 'ALM-0034 분석 시작' },
  { ts: '2026-06-03 22:26:48', actor: 'fdc-agent', ac: 'AGENT', ev: 'CLASSIFICATION_COMPLETED', entType: 'agent_run', entId: 'RUN-0009', summary: 'Fault Code FOC 분류' },
  { ts: '2026-06-03 22:26:49', actor: 'fdc-agent', ac: 'AGENT', ev: 'AGENT_RUN_COMPLETED', entType: 'agent_run', entId: 'RUN-0009', summary: '분석 완료 · ACT-0009' },
  { ts: '2026-06-03 06:37:49', actor: 'fdc-agent', ac: 'AGENT', ev: 'AGENT_RUN_STARTED', entType: 'agent_run', entId: 'RUN-0006', summary: 'ALM-0019 분석 시작' },
  { ts: '2026-06-03 06:37:56', actor: 'fdc-agent', ac: 'AGENT', ev: 'CLASSIFICATION_COMPLETED', entType: 'agent_run', entId: 'RUN-0006', summary: 'Fault Code FOC 분류' },
  { ts: '2026-06-03 06:37:58', actor: 'fdc-agent', ac: 'AGENT', ev: 'APPROVAL_REQUESTED', entType: 'approval_request', entId: 'APR-0001', summary: 'EQP_HOLD 승인 요청', diff: diff('status', 'null', 'PENDING') },
  { ts: '2026-06-04 09:12:31', actor: 'daehyuk', ac: 'HUMAN', ev: 'APPROVAL_DECIDED', entType: 'approval_request', entId: 'APR-0001', summary: 'EQP_HOLD 승인', diff: diff('status', 'PENDING', 'APPROVED') },
  { ts: '2026-06-04 09:12:33', actor: 'SYSTEM', ac: 'SYSTEM', ev: 'ACTION_SENT', entType: 'action_history', entId: 'ACT-0005', summary: 'MES 전송 완료', diff: diff('send_status', 'WAITING', 'SENT') },
  { ts: '2026-06-02 07:20:25', actor: 'fdc-agent', ac: 'AGENT', ev: 'AGENT_RUN_STARTED', entType: 'agent_run', entId: 'RUN-0002', summary: 'ALM-0005 분석 시작' },
  { ts: '2026-06-02 07:20:55', actor: 'fdc-agent', ac: 'AGENT', ev: 'AGENT_RUN_FAILED', entType: 'agent_run', entId: 'RUN-0002', summary: 'search_documents TIMEOUT (30,012ms) — 자동 재처리 금지' },
  { ts: '2026-06-02 08:20:23', actor: 'fdc-agent', ac: 'AGENT', ev: 'AGENT_RUN_STARTED', entType: 'agent_run', entId: 'RUN-0003', summary: '수동 재실행 (RUN-0002 대체)' },
  { ts: '2026-06-02 08:20:32', actor: 'fdc-agent', ac: 'AGENT', ev: 'AGENT_RUN_COMPLETED', entType: 'agent_run', entId: 'RUN-0003', summary: '분석 완료 · ACT-0002' },
]
