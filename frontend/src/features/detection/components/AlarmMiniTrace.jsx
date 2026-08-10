// 미니 SPC 파형 — 시안 02_알람목록 SVG(viewBox 430×240)를 fixture trace 값→좌표 변환으로 재현
// 시안 좌표는 복사하지 않고 변환 함수로 계산한다.
// 검증: ALM-0022(LH-00125) EXPOSE [54.752, 52.193, 65.353] → y [41.8, 44.8, 29.3] (±1px)
const STEP_ORDER = ['EXPOSE', 'DEVELOP', 'MAIN_ETCH']
const PTS_PER_STEP = 3

// y 선형 변환: 값 60 → y 35.6 / 0 → 106.5 / −60 → 177.3 (y = 106.5 − v × 70.9/60)
const Y_ZERO = 106.5
const PX_PER_UNIT = 70.9 / 60

// x: 슬롯 등간격 — 6슬롯이면 60, 122, 184, 246, 308, 370
const X_FIRST = 60
const X_LAST = 370
const LINE_X1 = 40
const LINE_X2 = 385

const LIMIT_STYLE = {
  USL: { line: 'stroke-red', label: 'fill-red', dash: '4 4' },
  UCL: { line: 'stroke-amber', label: 'fill-amber', dash: '4 4' },
  TARGET: { line: 'stroke-g2', label: 'fill-g2' },
  LCL: { line: 'stroke-amber', label: 'fill-amber', dash: '4 4' },
  LSL: { line: 'stroke-red', label: 'fill-red', dash: '4 4' },
}

const stepRank = (name) => {
  const i = STEP_ORDER.indexOf(name)
  return i === -1 ? STEP_ORDER.length : i
}

function Note({ children }) {
  return (
    <div className="rounded-lg border border-dashed border-dash-line bg-soft px-3 py-2 text-xs text-g1">
      {children}
    </div>
  )
}

function AlarmMiniTrace({ wafer, limits }) {
  if (!wafer) {
    // TODO(data): 이 lot_hist_id의 trace 실측이 fixture에 없다 — fdc_trace.csv 연결 시 채워진다
    return <Note>이 웨이퍼의 trace 실측 미제공</Note>
  }

  const limitList = limits ?? []
  const usl = limitList.find((l) => l.label === 'USL')?.value ?? 60

  const stepNames = Object.keys(wafer.steps ?? {}).sort((a, b) => stepRank(a) - stepRank(b))
  const drawnSteps = stepNames.filter(
    (n) => Array.isArray(wafer.steps[n]?.points) && wafer.steps[n].points.length > 0,
  )
  // points: null 스텝은 그리지 않는다 (OOC 알람은 detail에 mean만 있어 포인트 복원 불가)
  const missing = stepNames.filter((n) => !drawnSteps.includes(n))

  if (drawnSteps.length === 0) {
    return <Note>{missing.join(' · ')} 해당 스텝 실측 미제공</Note>
  }

  const values = drawnSteps.flatMap((n) => wafer.steps[n].points)
  // 기본 스케일로 상단(값 라벨 포함 y≥24)을 벗어나는 극단값 웨이퍼만 스케일을 축소한다.
  // ALM-0022 검수 케이스(max 65.353)는 기본 스케일 그대로 시안 좌표와 일치한다.
  const maxV = Math.max(...values)
  const minV = Math.min(...values)
  let scale = PX_PER_UNIT
  if (maxV > 0) scale = Math.min(scale, (Y_ZERO - 24) / maxV)
  if (minV < 0) scale = Math.min(scale, (200 - Y_ZERO) / -minV)
  const yOf = (v) => Y_ZERO - v * scale

  const slotCnt = drawnSteps.length * PTS_PER_STEP
  const xOf = (i) => (slotCnt === 1 ? (X_FIRST + X_LAST) / 2 : X_FIRST + (i * (X_LAST - X_FIRST)) / (slotCnt - 1))

  const pts = []
  drawnSteps.forEach((name, si) => {
    wafer.steps[name].points.forEach((v, pi) => {
      pts.push({ x: xOf(si * PTS_PER_STEP + pi), y: yOf(v), v, oos: v > usl })
    })
  })
  // 스텝 경계 세로 점선 — 이웃 슬롯의 중앙 (6슬롯 2스텝이면 x=215)
  const seps = drawnSteps.slice(1).map((_, k) => {
    const b = (k + 1) * PTS_PER_STEP
    return (xOf(b - 1) + xOf(b)) / 2
  })

  return (
    <div>
      <svg viewBox="0 0 430 240" className="block w-full font-mono">
        {limitList.map((l) => {
          const st = LIMIT_STYLE[l.label] ?? LIMIT_STYLE.TARGET
          const y = yOf(l.value)
          return (
            <g key={l.label}>
              <line
                x1={LINE_X1}
                y1={y}
                x2={LINE_X2}
                y2={y}
                strokeWidth="1"
                strokeDasharray={st.dash}
                opacity={st.dash ? 0.7 : 1}
                className={st.line}
              />
              <text x={LINE_X1 - 6} y={y + 3.5} fontSize="9" textAnchor="end" className="fill-g1">
                {l.value}
              </text>
              <text x={LINE_X2 + 5} y={y + 3.5} fontSize="8.5" className={st.label}>
                {l.label}
              </text>
            </g>
          )
        })}
        {seps.map((x) => (
          <line key={x} x1={x} y1="20" x2={x} y2="200" strokeWidth="1" strokeDasharray="3 4" opacity="0.6" className="stroke-g2" />
        ))}
        <polyline points={pts.map((p) => `${p.x},${p.y}`).join(' ')} fill="none" strokeWidth="1.8" className="stroke-blue" />
        {pts.map((p) => (
          <g key={p.x}>
            <circle
              cx={p.x}
              cy={p.y}
              r={p.oos ? 4.5 : 4}
              strokeWidth="1.8"
              className={p.oos ? 'fill-red stroke-red' : 'fill-white stroke-blue'}
            />
            {p.oos && (
              <text x={p.x} y={p.y - 13.3} fontSize="11" fontWeight="700" textAnchor="middle" className="fill-red">
                {p.v}
              </text>
            )}
          </g>
        ))}
        {drawnSteps.map((name, si) => (
          <text key={name} x={xOf(si * PTS_PER_STEP + 1)} y="222" fontSize="9.5" textAnchor="middle" className="fill-g1">
            {name}
          </text>
        ))}
      </svg>
      {missing.length > 0 && (
        <div className="mb-1.5 mt-1">
          <Note>{missing.join(' · ')} 해당 스텝 실측 미제공</Note>
        </div>
      )}
    </div>
  )
}

export default AlarmMiniTrace
