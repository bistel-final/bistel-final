// Agent 대표 R03 incident의 최종 데이터 기반 화면 fixture.
// 근거: 멘토님 제공 최종 project.zip의 lot_history.csv, fdc_trace.csv,
// trace_alarm_history.csv, dim_parameter.csv. 화면 확인을 위해 측정값을 합성하지 않는다.

export const FINAL_AGENT_TRACE_LIMIT = Object.freeze({
  sensor_id: 'ET_REFL',
  sensor_name: 'Reflected Power',
  unit: 'W',
  spec_lower: 0,
  ctrl_lower: 0,
  target: 8,
  ctrl_upper: 21,
  spec_upper: 30,
})

const tracePoints = (values, startedAt) => values.map((value, seq_no) => ({
  seq_no,
  recipe_step_no: seq_no < 3 ? 1 : 2,
  recipe_step_name: seq_no < 3 ? 'MAIN_ETCH' : 'OVER_ETCH',
  measured_at: new Date(new Date(startedAt).getTime() + seq_no * 5_000).toISOString(),
  value,
}))

const FINAL_AGENT_TRACE_ROWS = [
  ['LH-00176', 1, 'EQP04-PM1', '2026-08-04T06:50:32+09:00', [13.088, 16.02, 2.565, 1.327, 1.778, 16.744]],
  ['LH-00177', 2, 'EQP04-PM2', '2026-08-04T06:52:29+09:00', [37.467, 30.174, 31.763, 9.425, 15.825, 2.573]],
  ['LH-00178', 3, 'EQP04-PM1', '2026-08-04T06:54:23+09:00', [14.859, 2.283, 3.154, 11.771, 5.083, 3.408]],
  ['LH-00179', 4, 'EQP04-PM2', '2026-08-04T06:56:15+09:00', [34.042, 30.351, 35.218, 4.127, 20.457, 6.008]],
  ['LH-00180', 5, 'EQP04-PM1', '2026-08-04T06:57:58+09:00', [-0.256, 7.991, 9.071, 7.839, 9.422, 7.746]],
  ['LH-00181', 6, 'EQP04-PM2', '2026-08-04T07:00:04+09:00', [31.758, 33.656, 32.397, 0.974, 7.212, 8.007]],
  ['LH-00182', 7, 'EQP04-PM1', '2026-08-04T07:02:00+09:00', [15.856, 15.637, 14.769, -0.503, 5.471, 7.96]],
  ['LH-00183', 8, 'EQP04-PM2', '2026-08-04T07:03:49+09:00', [30.487, 31.934, 31.472, 8.898, 6.181, 4.611]],
  ['LH-00184', 9, 'EQP04-PM1', '2026-08-04T07:05:39+09:00', [0.004, 1.77, 11.741, 9.857, 12.481, 11.511]],
  ['LH-00185', 10, 'EQP04-PM2', '2026-08-04T07:07:31+09:00', [33.435, 31.345, 32.025, 4.976, 5.99, 7.305]],
  ['LH-00186', 11, 'EQP04-PM1', '2026-08-04T07:09:25+09:00', [8.359, 0.408, 3.592, 6.966, 3.479, 9.181]],
  ['LH-00187', 12, 'EQP04-PM2', '2026-08-04T07:11:16+09:00', [30.384, 33.154, 32.388, -0.377, 8.234, 10.949]],
  ['LH-00188', 13, 'EQP04-PM1', '2026-08-04T07:13:00+09:00', [5.119, 9.385, 7.32, 11.624, 3.219, 7.228]],
  ['LH-00189', 14, 'EQP04-PM2', '2026-08-04T07:15:05+09:00', [30.959, 32.062, 33, 10.838, 9.138, -1.26]],
  ['LH-00190', 15, 'EQP04-PM1', '2026-08-04T07:16:58+09:00', [3.297, -1.611, 6.644, 22.092, 5.808, 14.036]],
  ['LH-00191', 16, 'EQP04-PM2', '2026-08-04T07:18:45+09:00', [32.467, 33.432, 33.32, 7.83, 11.209, 8.041]],
  ['LH-00192', 17, 'EQP04-PM1', '2026-08-04T07:20:38+09:00', [13.154, 7.911, 8.622, -4.858, 13.972, 0.847]],
  ['LH-00193', 18, 'EQP04-PM2', '2026-08-04T07:22:16+09:00', [30.736, 33.284, 34.541, 2.804, 14.115, 8.229]],
  ['LH-00194', 19, 'EQP04-PM1', '2026-08-04T07:24:12+09:00', [3.452, 2.554, 12.135, 11.386, 8.139, 15.091]],
  ['LH-00195', 20, 'EQP04-PM2', '2026-08-04T07:26:09+09:00', [35.854, 31.42, 36.221, 4.47, 10.523, 15.112]],
  ['LH-00196', 21, 'EQP04-PM1', '2026-08-04T07:28:05+09:00', [-2.366, 3.765, 12.527, 10.311, 12.549, 1.878]],
  ['LH-00197', 22, 'EQP04-PM2', '2026-08-04T07:29:50+09:00', [30.958, 30.22, 30.152, 9.085, 1.666, 10.51]],
  ['LH-00198', 23, 'EQP04-PM1', '2026-08-04T07:31:38+09:00', [10.203, 7.693, 6.794, 12.855, 2.775, 10.045]],
  ['LH-00199', 24, 'EQP04-PM2', '2026-08-04T07:33:47+09:00', [31.136, 35.718, 32.024, 13.446, 2.589, 8.164]],
  ['LH-00200', 25, 'EQP04-PM1', '2026-08-04T07:35:26+09:00', [7.474, 4.197, -1.449, 12.649, 9.505, -1.563]],
]

export const FINAL_AGENT_WAFER_TRACES = Object.freeze(
  FINAL_AGENT_TRACE_ROWS.map(([lot_hist_id, wafer_no, chamber_id, occurred_at, values]) => Object.freeze({
    lot_hist_id,
    lot_id: 'LOT004',
    wafer_no,
    chamber_id,
    equipment_id: 'EQP04',
    sensor_id: 'ET_REFL',
    recipe_id: 'RECIPE04',
    occurred_at,
    points: tracePoints(values, occurred_at),
  })),
)

export const FINAL_AGENT_INCIDENT_ALARMS = Object.freeze([
  ['TAL-0001', 'LH-00177', 2, '2026-08-04T06:52:29+09:00', [37.467, 30.174, 31.763]],
  ['TAL-0004', 'LH-00179', 4, '2026-08-04T06:56:15+09:00', [34.042, 30.351, 35.218]],
  ['TAL-0007', 'LH-00181', 6, '2026-08-04T07:00:04+09:00', [31.758, 33.656, 32.397]],
].map(([alarm_id, lot_hist_id, wafer_no, occurred_at, values]) => Object.freeze({
  alarm_id,
  source: 'TRACE',
  occurred_at,
  area: 'Etch',
  equipment_id: 'EQP04',
  chamber_id: 'EQP04-PM2',
  sensor_id: 'ET_REFL',
  lot_hist_id,
  lot_id: 'LOT004',
  wafer_no,
  recipe_step_no: 1,
  recipe_step_name: 'MAIN_ETCH',
  rule_id: 'R01_OOS',
  judgement: 'OOS',
  hit_cnt: 3,
  detail: `OOS 3 points at MAIN_ETCH (mean ${(values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(3)}, min ${Math.min(...values)}, max ${Math.max(...values)})`,
  incident: { lot_id: 'LOT004', chamber_id: 'EQP04-PM2' },
  action_id: null,
  action_code: null,
  approval_status: null,
  latest_agent_run_id: null,
  agent_run_status: null,
})))
