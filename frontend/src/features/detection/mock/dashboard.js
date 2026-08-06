// dc.html 대시보드 mock — AREA별 KPI·일자별 추이 (ETCH+PHOTO = 전체와 합계 일치)
export const DASHBOARD = {
  date: '2026-06-04',
  areas: ['전체', 'PHOTO', 'ETCH'],
  kpiByArea: {
    전체: { today: 6, todayOos: 6, todayOoc: 0, total: 51, totalOos: 37, totalOoc: 14 },
    ETCH: { today: 6, todayOos: 6, todayOoc: 0, total: 29, totalOos: 20, totalOoc: 9 },
    PHOTO: { today: 0, todayOos: 0, todayOoc: 0, total: 22, totalOos: 17, totalOoc: 5 },
  },
  pending: 2,
  passRate: 85,
  passMeta: '34 / 40',
  active: 10,
  dayByArea: {
    전체: [
      { label: '6/1', oos: 0, ooc: 4 },
      { label: '6/2', oos: 11, ooc: 3 },
      { label: '6/3', oos: 20, ooc: 7 },
      { label: '6/4', oos: 6, ooc: 0 },
    ],
    ETCH: [
      { label: '6/1', oos: 0, ooc: 2 },
      { label: '6/2', oos: 4, ooc: 1 },
      { label: '6/3', oos: 10, ooc: 6 },
      { label: '6/4', oos: 6, ooc: 0 },
    ],
    PHOTO: [
      { label: '6/1', oos: 0, ooc: 2 },
      { label: '6/2', oos: 7, ooc: 2 },
      { label: '6/3', oos: 10, ooc: 1 },
      { label: '6/4', oos: 0, ooc: 0 },
    ],
  },
  chambers: [
    { name: 'PHO-01-C1', area: 'PHOTO', status: 'ALARM', note: 'PHOTO · OOS 알람 발생', hold: false },
    { name: 'PHO-01-C2', area: 'PHOTO', status: 'NORMAL', note: 'PHOTO · 이상 없음', hold: false },
    { name: 'ETC-01-C1', area: 'ETCH', status: 'CRITICAL', note: 'ETCH · R03_CONSEC 연속 이상', hold: true },
    { name: 'ETC-01-C2', area: 'ETCH', status: 'ALARM', note: 'ETCH · OOS 알람 발생', hold: false },
  ],
  // 최근 알람 (실측 시각) — 전체/ETCH는 ETCH 최신 5건, PHOTO는 PHOTO 최신 5건
  recentByArea: {
    전체: [
      { id: 'ALM-0051', sensor: 'ET_CF4', eqp: 'ETC-01-C1', rule: 'R01_OOS', time: '07:29', crit: false },
      { id: 'ALM-0050', sensor: 'ET_CF4', eqp: 'ETC-01-C1', rule: 'R01_OOS', time: '07:25', crit: false },
      { id: 'ALM-0049', sensor: 'ET_CF4', eqp: 'ETC-01-C1', rule: 'R01_OOS', time: '07:20', crit: false },
      { id: 'ALM-0048', sensor: 'ET_CF4', eqp: 'ETC-01-C1', rule: 'R03_CONSEC', time: '07:15', crit: true },
      { id: 'ALM-0047', sensor: 'ET_CF4', eqp: 'ETC-01-C1', rule: 'R01_OOS', time: '07:15', crit: false },
    ],
    PHOTO: [
      { id: 'ALM-0040', sensor: 'PH_FOCUS', eqp: 'PHO-01-C1', rule: 'R01_OOS', time: '6/3 22:42', crit: false },
      { id: 'ALM-0039', sensor: 'PH_FOCUS', eqp: 'PHO-01-C1', rule: 'R01_OOS', time: '6/3 22:38', crit: false },
      { id: 'ALM-0038', sensor: 'PH_FOCUS', eqp: 'PHO-01-C1', rule: 'R01_OOS', time: '6/3 22:38', crit: false },
      { id: 'ALM-0037', sensor: 'PH_FOCUS', eqp: 'PHO-01-C1', rule: 'R01_OOS', time: '6/3 22:34', crit: false },
      { id: 'ALM-0035', sensor: 'PH_FOCUS', eqp: 'PHO-01-C1', rule: 'R02_OOC', time: '6/3 22:26', crit: false },
    ],
  },
}
DASHBOARD.recentByArea.ETCH = DASHBOARD.recentByArea['전체']
