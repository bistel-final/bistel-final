// dc.html 관계 그래프 — PHO-01(PH-9000) → UPSTREAM_OF → ETC-01(ET-7500)
// 챔버별 알람 통계는 alarms-data.js 51건 실측 집계값
export const RELATIONS = {
  equipments: [
    { id: 'PHO-01', model: 'PH-9000', process: 'PHOTO 공정', group: 'pho' },
    { id: 'ETC-01', model: 'ET-7500', process: 'ETCH 공정', group: 'etc' },
  ],
  edge: { from: 'PHO-01', to: 'ETC-01', type: 'UPSTREAM_OF' },
  chambers: [
    { name: 'PHO-01-C1', group: 'pho', status: 'ALARM', hold: false, total: 22, oos: 17, ooc: 5 },
    { name: 'PHO-01-C2', group: 'pho', status: 'NORMAL', hold: false, total: 0, oos: 0, ooc: 0 },
    { name: 'ETC-01-C1', group: 'etc', status: 'CRITICAL', hold: true, total: 14, oos: 9, ooc: 5 },
    { name: 'ETC-01-C2', group: 'etc', status: 'ALARM', hold: false, total: 15, oos: 11, ooc: 4 },
  ],
}
