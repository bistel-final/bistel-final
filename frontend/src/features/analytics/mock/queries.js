// 자연어 분석 mock — 예시 칩·최근 질의는 디자인 v2 06 시안, 집계값은 alarms 51건 실측
export const NL_CHIPS = [
  '전체 알람이 몇 건이야?',
  '센서별 OOS 알람 수',
  'ETCH 챔버별 CD_AEI 평균',
  '규칙별 알람 분포',
  '알람 테이블 전부 지워줘',
]

export const NL_REJECT_REASONS = {
  REJECT_NON_SELECT: 'SELECT 외 구문 거부',
  REJECT_TABLE: '허용 목록 밖 테이블',
}

// TODO(data): 'ETCH 챔버별 CD_AEI 평균'은 metrology 평균 실측이 없어 mock 미제공 —
//   질문 시 안내 카드가 뜬다. CSV 확보 후 def 추가.
export const NL_QUERIES = {
  '전체 알람이 몇 건이야?': {
    sql: 'SELECT COUNT(*) AS alarm_cnt\nFROM fdc_alarm\nLIMIT 500',
    cols: ['alarm_cnt'],
    rows: [[51]],
    chart: 'demote',
    visualization: { chart_type: 'table', x: null, y: 'alarm_cnt' },
    lat: 1240,
  },
  '챔버별 알람 건수 내림차순': {
    sql: 'SELECT chamber_id, COUNT(*) AS cnt\nFROM fdc_alarm\nGROUP BY chamber_id\nORDER BY cnt DESC\nLIMIT 500',
    cols: ['chamber_id', 'cnt'],
    rows: [
      ['PHO-01-C1', 22],
      ['ETC-01-C2', 15],
      ['ETC-01-C1', 14],
    ],
    chart: 'bar',
    visualization: { chart_type: 'bar', x: 'chamber_id', y: 'cnt' },
    stats: [
      ['count', '3'],
      ['mean', '17.0'],
      ['std', '4.36'], // ddof=1 표본 표준편차
      ['min', '14'],
      ['max', '22'],
    ],
    lat: 940,
  },
  '센서별 OOS 알람 수': {
    sql: "SELECT sensor_id, COUNT(*) AS cnt\nFROM fdc_alarm\nWHERE judgement = 'OOS'\nGROUP BY sensor_id\nORDER BY cnt DESC\nLIMIT 500",
    cols: ['sensor_id', 'cnt'],
    rows: [
      ['PH_FOCUS', 17],
      ['ET_REFL', 11],
      ['ET_CF4', 9],
    ],
    chart: 'bar',
    visualization: { chart_type: 'bar', x: 'sensor_id', y: 'cnt' },
    lat: 870,
  },
  '규칙별 알람 분포': {
    sql: 'SELECT rule_id, COUNT(*) AS cnt\nFROM fdc_alarm\nGROUP BY rule_id\nORDER BY cnt DESC\nLIMIT 500',
    cols: ['rule_id', 'cnt'],
    rows: [
      ['R01_OOS', 34],
      ['R02_OOC', 14],
      ['R03_CONSEC', 3],
    ],
    chart: 'bar',
    visualization: { chart_type: 'bar', x: 'rule_id', y: 'cnt' },
    lat: 1655,
  },
  '알람 테이블 전부 지워줘': { reject: true, reject_code: 'REJECT_NON_SELECT', lat: 310 },
  'document_chunk 보여줘': { reject: true, reject_code: 'REJECT_TABLE', lat: 120 },
}

// 최근 질의 초기 이력 — 디자인 v2 06 시안 5건 그대로 (성공 3 · 거부 2)
export const NL_INITIAL_HISTORY = [
  { q: '챔버별 알람 건수 내림차순', ok: true, rows: 3, lat: 940 },
  { q: '전체 알람이 몇 건이야?', ok: true, rows: 1, lat: 1240 },
  { q: '알람 테이블 전부 지워줘', ok: false, rows: 0, lat: 310, code: 'REJECT_NON_SELECT' },
  { q: '센서별 OOS 알람 수', ok: true, rows: 3, lat: 870 },
  { q: 'document_chunk 보여줘', ok: false, rows: 0, lat: 120, code: 'REJECT_TABLE' },
]
