import { LineChart, Line, XAxis, YAxis, ReferenceLine, ResponsiveContainer } from 'recharts'

// 미니 파형 — 스텝당 3포인트를 순서대로 이어 그린다 (EXPOSE 3 → DEVELOP 3 = 6점)
// 한계선은 getTraceCatalog().limits (USL 60 / UCL 36 / TARGET 0 / LCL −36 / LSL −60)
const STEP_ORDER = ['EXPOSE', 'DEVELOP', 'MAIN_ETCH']
const PTS_PER_STEP = 3

const LIMIT_STYLE = {
  USL: { color: '#DC2626', width: 2, dash: '6 4' },
  LSL: { color: '#DC2626', width: 2, dash: '6 4' },
  UCL: { color: '#D97706', width: 1.5, dash: '5 4' },
  LCL: { color: '#D97706', width: 1.5, dash: '5 4' },
  TARGET: { color: '#94A3B8', width: 1.5, dash: undefined },
}

const stepRank = (name) => {
  const i = STEP_ORDER.indexOf(name)
  return i === -1 ? STEP_ORDER.length : i
}

// USL 초과 = OOS(적색) · UCL 초과 = OOC(앰버) · 그 외 정상(brand)
const colorOf = (v, usl, ucl) => (v > usl ? '#DC2626' : v > ucl ? '#D97706' : '#1E5FC2')

const TraceDot = ({ cx, cy, payload }) => {
  if (cx == null || cy == null) return null
  return (
    <g>
      <circle cx={cx} cy={cy} r={6} fill="none" stroke={payload.color} strokeWidth={1.5} />
      <circle cx={cx} cy={cy} r={3.5} fill={payload.color} stroke="#FFFFFF" strokeWidth={1.5} />
      <text
        x={cx}
        y={cy - 11}
        textAnchor="middle"
        fill={payload.color}
        fontSize={10.5}
        fontWeight={800}
        fontFamily="ui-monospace, SF Mono, Menlo, monospace"
      >
        {payload.y.toFixed(1)}
      </text>
    </g>
  )
}

function AlarmMiniTrace({ wafer, limits }) {
  if (!wafer) {
    // TODO(data): 이 lot_hist_id의 trace 실측이 fixture에 없다 — fdc_trace.csv 연결 시 채워진다
    return (
      <div className="rounded-[10px] border border-dashed border-line-input bg-page px-4 py-6 text-center text-[13px] font-semibold text-slate">
        이 웨이퍼의 trace 실측 미제공
      </div>
    )
  }

  const limitList = limits ?? []
  const usl = limitList.find((l) => l.label === 'USL')?.value ?? 60
  const ucl = limitList.find((l) => l.label === 'UCL')?.value ?? 36

  const stepNames = Object.keys(wafer.steps ?? {}).sort((a, b) => stepRank(a) - stepRank(b))
  const drawn = []
  const missing = []
  stepNames.forEach((name, si) => {
    const pts = wafer.steps[name]?.points
    if (!Array.isArray(pts) || pts.length === 0) {
      missing.push(name)
      return
    }
    pts.forEach((v, pi) => {
      drawn.push({ n: si * PTS_PER_STEP + pi + 1, y: v, step: name, color: colorOf(v, usl, ucl) })
    })
  })

  const slotCnt = Math.max(stepNames.length * PTS_PER_STEP, PTS_PER_STEP)
  const limVals = limitList.map((l) => l.value)
  const lo = Math.min(...limVals, ...drawn.map((p) => p.y))
  const hi = Math.max(...limVals, ...drawn.map((p) => p.y))
  const pad = Math.max(10, (hi - lo) * 0.12)

  return (
    <div>
      {drawn.length > 0 ? (
        <div className="h-[196px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={drawn} margin={{ top: 18, right: 40, bottom: 4, left: 0 }}>
              {limitList.map((l) => {
                const st = LIMIT_STYLE[l.label] ?? LIMIT_STYLE.TARGET
                return (
                  <ReferenceLine
                    key={l.label}
                    y={l.value}
                    stroke={st.color}
                    strokeWidth={st.width}
                    strokeDasharray={st.dash}
                    label={{
                      value: l.label,
                      position: 'right',
                      fill: st.color === '#94A3B8' ? '#64748B' : st.color,
                      fontSize: 10,
                      fontWeight: 800,
                      fontFamily: 'ui-monospace, SF Mono, Menlo, monospace',
                    }}
                  />
                )
              })}
              {stepNames.slice(1).map((name, i) => (
                <ReferenceLine key={`sep-${name}`} x={(i + 1) * PTS_PER_STEP + 0.5} stroke="#CBD5E1" strokeDasharray="4 4" />
              ))}
              <XAxis
                dataKey="n"
                type="number"
                domain={[0.5, slotCnt + 0.5]}
                ticks={Array.from({ length: slotCnt }, (_, i) => i + 1)}
                tickFormatter={(n) => `P${n}`}
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#64748B', fontSize: 10.5, fontWeight: 700, fontFamily: 'ui-monospace, Menlo, monospace' }}
              />
              <YAxis
                type="number"
                domain={[Math.floor(lo - pad), Math.ceil(hi + pad)]}
                ticks={limVals}
                axisLine={false}
                tickLine={false}
                width={38}
                tick={{ fill: '#64748B', fontSize: 10, fontWeight: 700, fontFamily: 'ui-monospace, Menlo, monospace' }}
              />
              <Line dataKey="y" stroke="#1E5FC2" strokeWidth={2.5} dot={TraceDot} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="rounded-[10px] border border-dashed border-line-input bg-page px-4 py-6 text-center text-[13px] font-semibold text-slate">
          이 웨이퍼의 포인트 실측 미제공
        </div>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[11.5px] font-bold text-slate">
        {stepNames.map((name) => (
          <span key={name} className="font-mono text-[11px] font-extrabold text-navy">
            {name}
          </span>
        ))}
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-oos" />
          OOS
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-ooc" />
          OOC
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-brand" />
          정상
        </span>
      </div>
      {missing.length > 0 && (
        // TODO(data): OOC 알람은 detail에 mean만 있어 포인트 복원이 불가하다
        <div className="mt-2 rounded-lg border border-dashed border-line-input bg-page px-3 py-2 text-[12px] font-semibold text-slate">
          {missing.join(' · ')} 해당 스텝 실측 미제공
        </div>
      )}
    </div>
  )
}

export default AlarmMiniTrace
