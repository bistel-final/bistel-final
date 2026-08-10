// 미니 SPC 파형 — 시안 02_알람목록 SVG(viewBox 430×240)를 trace 값→좌표 변환으로 재현
// 시안 좌표는 복사하지 않고 변환 함수로 계산한다.
//
// 데이터는 POST /traces/search 응답의 wafer 1건({points:[{seq_no, recipe_step_name, value}]})과
// 센서별 한계선 limits[sensor_id]({spec_lower, ctrl_lower, target, ctrl_upper, spec_upper, unit}) 다.
// 전역 한계선 상수를 두면 ET_CF4 에 PH_FOCUS 한계선을 쓰는 사고가 난다 — 반드시 응답 값을 쓴다.
//
// 검증: ALM-0022(LH-00125) EXPOSE [54.753, 52.193, 65.353] → y [41.8, 44.8, 29.3] (±1px)

// spec_upper → y 35.6 / spec_lower → y 177.4 로 매핑한다 (PH_FOCUS ±60 이면 0 → 106.5).
const Y_SPEC_UPPER = 35.6
const Y_SPEC_LOWER = 177.4
// 값 라벨(점 위 13.3px)과 스텝 라벨이 잘리지 않는 그리기 영역
const Y_DRAW_TOP = 24
const Y_DRAW_BOT = 200

// x: 슬롯 등간격 — 6슬롯이면 60, 122, 184, 246, 308, 370
const X_FIRST = 60
const X_LAST = 370
const LINE_X1 = 40
const LINE_X2 = 385

// 한계선 5종 — 응답 필드명과 화면 라벨의 대응 (없는 값은 그리지 않는다)
const LIMIT_ROWS = [
  { label: 'USL', key: 'spec_upper', line: 'stroke-red', text: 'fill-red', dash: '4 4' },
  { label: 'UCL', key: 'ctrl_upper', line: 'stroke-amber', text: 'fill-amber', dash: '4 4' },
  { label: 'TARGET', key: 'target', line: 'stroke-g2', text: 'fill-g2' },
  { label: 'LCL', key: 'ctrl_lower', line: 'stroke-amber', text: 'fill-amber', dash: '4 4' },
  { label: 'LSL', key: 'spec_lower', line: 'stroke-red', text: 'fill-red', dash: '4 4' },
]

const num = (v) => typeof v === 'number' && Number.isFinite(v)

function Note({ children }) {
  return (
    <div className="rounded-lg border border-dashed border-dash-line bg-soft px-3 py-2 text-xs text-g1">
      {children}
    </div>
  )
}

function AlarmMiniTrace({ wafer, limit }) {
  if (!wafer) {
    // TODO(data): 이 웨이퍼의 trace 실측이 아직 없다 — fdc_trace 연결 시 채워진다
    return <Note>이 웨이퍼의 trace 실측 미제공</Note>
  }

  const points = [...(wafer.points ?? [])].sort((a, b) => a.seq_no - b.seq_no)
  // points 가 없는 스텝은 그리지 않는다 (OOC 알람은 detail 에 mean 만 있어 포인트 복원 불가)
  const missing = wafer.missing_steps ?? []

  if (points.length === 0) {
    return <Note>{missing.length ? `${missing.join(' · ')} 해당 스텝 ` : ''}실측 미제공</Note>
  }

  const values = points.map((p) => p.value)
  const maxV = Math.max(...values)
  const minV = Math.min(...values)

  const specUpper = num(limit?.spec_upper) ? limit.spec_upper : null
  const specLower = num(limit?.spec_lower) ? limit.spec_lower : null
  const hasSpec = specUpper !== null && specLower !== null && specUpper > specLower

  let yOf
  if (hasSpec) {
    // 규격 폭을 캔버스 상·하단에 매핑한다 — 센서 단위가 무엇이든 같은 식으로 동작한다
    const anchorV = num(limit.target) ? limit.target : (specUpper + specLower) / 2
    const base = (Y_SPEC_LOWER - Y_SPEC_UPPER) / (specUpper - specLower)
    const yAnchor = Y_SPEC_UPPER + (specUpper - anchorV) * base
    // 규격을 크게 벗어난 웨이퍼만 스케일을 줄여 캔버스 안에 넣는다
    let scale = base
    if (maxV > anchorV) scale = Math.min(scale, (yAnchor - Y_DRAW_TOP) / (maxV - anchorV))
    if (minV < anchorV) scale = Math.min(scale, (Y_DRAW_BOT - yAnchor) / (anchorV - minV))
    yOf = (v) => yAnchor - (v - anchorV) * scale
  } else {
    // TODO(data): 이 센서의 한계선 미확보 — 값 범위만으로 스케일을 잡는다
    const span = maxV - minV || 1
    yOf = (v) => Y_DRAW_BOT - ((v - minV) / span) * (Y_DRAW_BOT - Y_DRAW_TOP)
  }

  const lines = LIMIT_ROWS.map((r) => ({ ...r, value: limit?.[r.key] })).filter((r) => num(r.value))
  const unit = limit?.unit ?? null

  const slotCnt = points.length
  const xOf = (i) => (slotCnt === 1 ? (X_FIRST + X_LAST) / 2 : X_FIRST + (i * (X_LAST - X_FIRST)) / (slotCnt - 1))

  const isOos = (v) => (specUpper !== null && v > specUpper) || (specLower !== null && v < specLower)
  const pts = points.map((p, i) => ({
    key: p.seq_no ?? i,
    x: xOf(i),
    y: yOf(p.value),
    v: p.value,
    oos: isOos(p.value),
  }))

  // 스텝 경계 — recipe_step_name 이 바뀌는 두 슬롯의 중앙 (6슬롯 2스텝이면 x=215)
  const seps = []
  const steps = []
  points.forEach((p, i) => {
    const prev = steps[steps.length - 1]
    if (prev && prev.name === p.recipe_step_name) {
      prev.to = i
      return
    }
    if (prev) seps.push((xOf(i - 1) + xOf(i)) / 2)
    steps.push({ name: p.recipe_step_name, from: i, to: i })
  })

  return (
    <div>
      <svg viewBox="0 0 430 240" className="block w-full font-mono">
        {lines.map((l) => {
          const y = yOf(l.value)
          return (
            <g key={l.label}>
              <line
                x1={LINE_X1}
                y1={y}
                x2={LINE_X2}
                y2={y}
                strokeWidth="1"
                strokeDasharray={l.dash}
                opacity={l.dash ? 0.7 : 1}
                className={l.line}
              />
              <text x={LINE_X1 - 6} y={y + 3.5} fontSize="9" textAnchor="end" className="fill-g1">
                {l.value}
              </text>
              <text x={LINE_X2 + 5} y={y + 3.5} fontSize="8.5" className={l.text}>
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
          <g key={p.key}>
            <circle
              cx={p.x}
              cy={p.y}
              r={p.oos ? 4.5 : 4}
              strokeWidth="1.8"
              className={p.oos ? 'fill-red stroke-red' : 'fill-white stroke-blue'}
            />
            {p.oos && (
              <text x={p.x} y={p.y - 13.3} fontSize="11" fontWeight="700" textAnchor="middle" className="fill-red">
                {unit ? `${p.v} ${unit}` : p.v}
              </text>
            )}
          </g>
        ))}
        {steps.map((s) => (
          <text
            key={s.name}
            x={(xOf(s.from) + xOf(s.to)) / 2}
            y="222"
            fontSize="9.5"
            textAnchor="middle"
            className="fill-g1"
          >
            {s.name}
          </text>
        ))}
      </svg>
      {!hasSpec && (
        <div className="mb-1.5 mt-1">
          <Note>한계선 미제공 — 값 범위로만 표시</Note>
        </div>
      )}
      {missing.length > 0 && (
        <div className="mb-1.5 mt-1">
          <Note>{missing.join(' · ')} 해당 스텝 실측 미제공</Note>
        </div>
      )}
    </div>
  )
}

export default AlarmMiniTrace
