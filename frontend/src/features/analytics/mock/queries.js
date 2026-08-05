// dc.html 자연어 분석 mock — 집계값은 alarms-data.js 51건 실측 기준
// 챔버별 건수: PHO-01-C1 22 / ETC-01-C2 15 / ETC-01-C1 14 (합 51)
export const NL_CHIPS = [
  '전체 알람이 몇 건이야?',
  '챔버별 알람 건수 내림차순',
  '계측 FAIL 결과 목록을 보여줘',
  '판정별 요약 건수',
  '알람 테이블 전부 지워줘',
]

export const NL_QUERIES = {
  '전체 알람이 몇 건이야?': {
    sql: 'SELECT COUNT(*) AS alarm_cnt\nFROM fdc_alarm;',
    cols: ['alarm_cnt'],
    rows: [[51]],
    chart: 'demote',
    lat: 1240,
  },
  '챔버별 알람 건수 내림차순': {
    sql: 'SELECT chamber_id, COUNT(*) AS alarm_cnt\nFROM fdc_alarm\nGROUP BY chamber_id\nORDER BY alarm_cnt DESC\nLIMIT 500;',
    cols: ['chamber_id', 'alarm_cnt'],
    rows: [
      ['PHO-01-C1', 22],
      ['ETC-01-C2', 15],
      ['ETC-01-C1', 14],
    ],
    chart: 'bar',
    stats: [
      ['count', '3'],
      ['mean', '17.0'],
      ['std', '4.36'], // ddof=1 표본 표준편차
      ['min', '14'],
      ['max', '22'],
    ],
    lat: 1873,
  },
  '계측 FAIL 결과 목록을 보여줘': {
    sql: "SELECT metrology_id, lot_id, wafer_no, measure_type, measured_value, judgement\nFROM metrology\nWHERE judgement = 'FAIL'\nLIMIT 500;",
    cols: ['metrology_id', 'lot_id', 'wafer_no', 'measure_type', 'measured_value', 'judgement'],
    rows: [
      ['MET-0016', 'LOT-260004', 'w6', 'CD_AEI', 41.82, 'FAIL'],
      ['MET-0020', 'LOT-260005', 'w6', 'CD_AEI', 41.41, 'FAIL'],
      ['MET-0029', 'LOT-260008', 'w1', 'CD_ADI', 41.59, 'FAIL'],
      ['MET-0031', 'LOT-260008', 'w1', 'CD_AEI', 41.2, 'FAIL'],
      ['MET-0033', 'LOT-260009', 'w1', 'CD_ADI', 41.4, 'FAIL'],
      ['MET-0035', 'LOT-260009', 'w1', 'CD_AEI', 39.59, 'FAIL'],
    ],
    noSort: true,
    chart: 'demote',
    lat: 1512,
  },
  '판정별 요약 건수': {
    sql: 'SELECT judgement, COUNT(*) AS cnt\nFROM fdc_alarm\nGROUP BY judgement\nORDER BY cnt DESC\nLIMIT 500;',
    cols: ['judgement', 'cnt'],
    rows: [
      ['OOS', 37],
      ['OOC', 14],
    ],
    chart: 'bar',
    corrected: true, // line → bar 보정 (범주형 x축)
    lat: 1655,
  },
  '알람 테이블 전부 지워줘': { reject: true, lat: 310 },
}

export const NL_INITIAL_HISTORY = [
  { q: '전체 알람이 몇 건이야?', ok: true, rows: 1, lat: 1240 },
  { q: '알람 테이블 전부 지워줘', ok: false, rows: 0, lat: 310 },
]
