import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

// 한계선 스타일 — 규격(USL/LSL)은 굵은 빨강, 관리(UCL/LCL)는 주황, TARGET은 회색
const LIMIT_STYLE = {
  USL: { color: '#DC2626', width: 2, dash: '7 5' },
  LSL: { color: '#DC2626', width: 2, dash: '7 5' },
  UCL: { color: '#D97706', width: 2, dash: '6 4' },
  LCL: { color: '#D97706', width: 2, dash: '6 4' },
  TARGET: { color: '#94A3B8', width: 1.5, dash: undefined },
}

const AXIS_TICK = {
  fill: '#64748B',
  fontSize: 11,
  fontWeight: 700,
  fontFamily: 'ui-monospace, SF Mono, Menlo, monospace',
}

const limitValue = (limits, label) => limits?.find((l) => l.label === label)?.value

// 포인트 판정: 규격 이탈 OOS(빨강) → 관리한계 초과 OOC(주황) → 정상(브랜드 블루)
function toneOf(v, limits) {
  const usl = limitValue(limits, 'USL')
  const lsl = limitValue(limits, 'LSL')
  const ucl = limitValue(limits, 'UCL')
  const lcl = limitValue(limits, 'LCL')
  if ((usl !== undefined && v > usl) || (lsl !== undefined && v < lsl)) return { color: '#DC2626', oos: true }
  if ((ucl !== undefined && v > ucl) || (lcl !== undefined && v < lcl)) return { color: '#D97706', oos: false }
  return { color: '#1E5FC2', oos: false }
}

function RunTraceChart({ rows, series, limits, height = 260 }) {
  const values = rows.flatMap((r) => series.map((s) => r[s.key]).filter((v) => typeof v === 'number'))
  const limitVals = (limits ?? []).map((l) => l.value)
  const lo = Math.min(...values, ...limitVals)
  const hi = Math.max(...values, ...limitVals)
  const pad = Math.max(6, (hi - lo) * 0.08)

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 18, right: 46, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="#EDF2FA" vertical={false} />
          {(limits ?? []).map((l) => {
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
                  fontSize: 10.5,
                  fontWeight: 800,
                  fontFamily: 'ui-monospace, SF Mono, Menlo, monospace',
                }}
              />
            )
          })}
          <XAxis
            dataKey="x"
            axisLine={false}
            tickLine={false}
            interval={0}
            tick={{ ...AXIS_TICK, fontSize: 10 }}
          />
          <YAxis
            type="number"
            domain={[Math.floor(lo - pad), Math.ceil(hi + pad)]}
            ticks={limitVals.length ? [...limitVals].sort((a, b) => a - b) : undefined}
            axisLine={false}
            tickLine={false}
            width={40}
            tick={AXIS_TICK}
          />
          <Tooltip
            contentStyle={{
              borderRadius: 10,
              border: '1px solid #E2E8F0',
              boxShadow: '0 4px 14px rgba(15,42,92,.10)',
              fontSize: 12,
              fontWeight: 700,
            }}
            labelStyle={{ color: '#0F2A5C', fontFamily: 'ui-monospace, Menlo, monospace', fontWeight: 800 }}
            formatter={(v, name) => [typeof v === 'number' ? v.toFixed(3) : v, name]}
          />
          {series.map((s) => (
            <Line
              key={s.key}
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={2}
              strokeDasharray={s.dash}
              connectNulls={false}
              isAnimationActive
              animationDuration={800}
              dot={
                s.statusDots
                  ? (props) => {
                      const { cx, cy, value, index } = props
                      if (typeof value !== 'number') return null
                      const t = toneOf(value, limits)
                      return (
                        <g key={`${s.key}-${index}`}>
                          <circle cx={cx} cy={cy} r={t.oos ? 5 : 4} fill={t.color} stroke="#FFFFFF" strokeWidth={2} />
                          {/* 라벨은 이탈 포인트에만 — 모든 점에 숫자를 붙이지 않는다 */}
                          {t.oos && (
                            <text
                              x={cx}
                              y={cy - 11}
                              textAnchor="middle"
                              fill={t.color}
                              fontSize={10.5}
                              fontWeight={800}
                              fontFamily="ui-monospace, SF Mono, Menlo, monospace"
                            >
                              {value.toFixed(1)}
                            </text>
                          )}
                        </g>
                      )
                    }
                  : { r: 3, fill: s.color, stroke: '#FFFFFF', strokeWidth: 1.5 }
              }
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export default RunTraceChart
