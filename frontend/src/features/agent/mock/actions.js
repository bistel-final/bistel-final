// dc.html 조치(action) 표시 문구 — 실측 조치 코드 기준
// EQP_HOLD 사유(why)는 센서별 fault 문구를 사용하므로 페이지에서 faultOf와 결합
export const ACTION_DISPLAY = {
  'ACT-0001': { code: 'MONITOR · AUTO', why: '자동 감시 유지(승인 불필요)' },
  'ACT-0002': { code: 'EQP_HOLD', why: null },
  'ACT-0003': { code: 'LOT_HOLD · AUTO', why: '해당 LOT 보류 — 자동 실행(승인 불필요)' },
  'ACT-0004': { code: 'MONITOR · AUTO', why: '자동 감시 유지(승인 불필요)' },
  'ACT-0005': { code: 'EQP_HOLD', why: null },
  'ACT-0006': { code: 'MONITOR · AUTO', why: '상류 원인 주 기여 — 하류 자체 OOC 감시 유지(승인 불필요)' },
  'ACT-0007': { code: 'LOT_HOLD · AUTO', why: '포커스 이탈 LOT 보류 — 자동 실행(승인 불필요)' },
  'ACT-0008': { code: 'LOT_HOLD · AUTO', why: '해당 LOT 보류 — 자동 실행(승인 불필요)' },
  'ACT-0009': { code: 'LOT_HOLD · AUTO', why: '포커스 이탈 LOT 보류 — 자동 실행(승인 불필요)' },
  'ACT-0010': { code: 'EQP_HOLD', why: null },
}

// 센서별 Fault 분류 (dc.html faultOf)
export const FAULT_BY_SENSOR = {
  PH_FOCUS: {
    code: 'FOC',
    name: '포커스 이탈',
    basis: 'PH_FOCUS 연속 OOS — 포커스 이탈로 CD_ADI 불량 유발',
    why: '포커스 이탈 지속 — CD_ADI 불량 방지 위해 장비 정지 필요',
  },
  ET_REFL: {
    code: 'RFM',
    name: 'RF 정합 이상',
    basis: '반사파 상승 = RF 정합 불량, 실효 전력 저하',
    why: 'RF 정합 불량 지속 — 실효 전력 저하로 공정 품질 위험',
  },
  ET_CF4: {
    code: 'MFD',
    name: 'CF4 유량 저하',
    basis: 'CF4 유량 저하로 식각 부족, 연속 3 WAFER OOS',
    why: 'CF4 유량 저하 — 식각 부족 재발 방지 위해 장비 정지 필요',
  },
}
