// dc.html 센서 Trace 실측 — PH_FOCUS 기준선 ±60/±36/0, LOT-260008 W1 6포인트
export const TRACE = {
  sensors: ['PH_DOSE', 'PH_FOCUS', 'PH_PEB', 'PH_DEV', 'ET_PRES', 'ET_REFL', 'ET_CF4', 'ET_ESC'],
  known: { lot: 'LOT-260008', wafer: '1', sensor: 'PH_FOCUS' },
  unit: 'nm',
  yDomain: [-70, 70],
  refLines: [
    { label: 'USL', value: 60 },
    { label: 'UCL', value: 36 },
    { label: 'TARGET', value: 0 },
    { label: 'LCL', value: -36 },
    { label: 'LSL', value: -60 },
  ],
  steps: ['EXPOSE', 'DEVELOP'],
  // step별 3*mean−min−max로 도출된 6포인트
  points: [50.323, 58.116, 64.16, 51.106, 46.954, 30.79],
  stepStats: [
    { name: 'EXPOSE', mean: '57.533', std: '6.937', min: '50.323', max: '64.160', pts: '3', ooc: '2', oos: '1', jud: 'OOS' },
    { name: 'DEVELOP', mean: '42.950', std: '10.733', min: '30.790', max: '51.106', pts: '3', ooc: '2', oos: '0', jud: 'OOC' },
  ],
  anomalyScore: 0.87,
  anomalyThreshold: 0.8,
}
