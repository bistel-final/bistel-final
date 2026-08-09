import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  ReferenceLine,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'
import { fmtShort, fmtTime } from '../../../shared/api/format.js'
import { POINT_COLOR, toneOfValue } from './TraceModel.jsx'

// 파라미터 1개당 차트 1개. x축은 measured_at 시간축이다(포인트 순번이 아니다).
// 웨이퍼 구간 경계는 실측 occurred_at, 구간 내부 포인트 간격은 렌더링 가정 — TraceModel.buildSeries 주석 참고.

const REF_STYLE = {
  USL: { color: '#DC2626', width: 2.5, dash: '7 5' },
  LSL: { color: '#DC2626', width: 2.5, dash: '7 5' },
  UCL: { color: '#D97706', width: 2, dash: '6 4' },
  LCL: { color: '#D97706', width: 2, dash: '6 4' },
  TARGET: { color: '#94A3B8', width: 1.5, dash: undefined },
}
const MONO = 'ui-monospace, SF Mono, Menlo, monospace'

const dotFor = (key, lim, focused) => (props) => {
  const { cx, cy, payload, index } = props
  const v = payload?.[key]
  const k = `${key}-${index}`
  if (v == null || cx == null || cy == null) return <g key={k} />
  const color = POINT_COLOR[toneOfValue(v, lim)]
  return (
    <g key={k}>
      {focused && <circle cx={cx} cy={cy} r={7.5} fill="none" stroke={color} strokeWidth={1.5} opacity={0.55} />}
      <circle cx={cx} cy={cy} r={focused ? 4.6 : 3.8} fill={color} stroke="#FFFFFF" strokeWidth={1.6} />
    </g>
  )
}

function TraceTooltip({ active, payload, lim }) {
  if (!active || !payload?.length) return null
  const row = payload[0]?.payload
  const p = row?.meta
  if (!p) return null
  const color = POINT_COLOR[toneOfValue(p.value, lim)]
  return (
    <div className="rounded-lg border border-line bg-white px-3 py-2 shadow-[0_4px_12px_rgba(15,42,92,.12)]">
      <div className="font-mono text-[12.5px] font-extrabold text-navy">
        W{p.waferNo} · {p.step} · P{p.seq}/{p.of}
      </div>
      <div className="mt-0.5 font-mono text-[15px] font-extrabold" style={{ color }}>
        {p.value.toFixed(3)} nm
      </div>
      <div className="mt-1 text-[11px] font-semibold text-slate-light">
        웨이퍼 실측시각 {fmtShort(p.waferAt)} · 구간 내 위치는 균등 배치 가정
      </div>
    </div>
  )
}

function TraceChart({ sensor, series, markers, limits, lim, focus }) {
  const { segments, points, missing } = series

  if (!points.length) {
    return (
      <div className="rounded-xl border border-line bg-white px-[22px] py-5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
        <div className="mb-2 font-mono text-[15px] font-extrabold text-navy">{sensor}</div>
        <div className="flex h-[180px] flex-col items-center justify-center gap-2 rounded-[10px] border-2 border-dashed border-line-input bg-page text-center">
          <div className="text-[15px] font-extrabold text-navy">포인트 실측 미제공</div>
          <div className="text-[13px] font-semibold text-slate">
            선택 웨이퍼의 모든 스텝이 <span className="font-mono">points: null</span> 이라 파형을 복원할 수 없습니다.
          </div>
        </div>
      </div>
    )
  }

  const data = points.map((p) => ({ x: p.x, [p.key]: p.value, meta: p }))
  const values = points.map((p) => p.value)
  const limitValues = limits.map((l) => l.value)
  const lo = Math.min(...values, ...limitValues)
  const hi = Math.max(...values, ...limitValues)
  const padY = Math.max(6, (hi - lo) * 0.08)
  const x0 = segments[0].start
  const x1 = segments[segments.length - 1].end
  const padX = Math.max(1, (x1 - x0) * 0.03)
  const tickLabel = new Map(segments.map((s) => [s.start, fmtTime(s.at)]))
  const focusedWafers = segments.filter((s) => focus.has(s.waferNo))

  return (
    <div className="rounded-xl border border-line bg-white px-[22px] py-5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="mb-3 flex flex-wrap items-baseline gap-2.5">
        <span className="font-mono text-[15px] font-extrabold text-navy">{sensor}</span>
        <span className="text-[12px] font-bold text-slate">
          웨이퍼 {segments.length}장 · 포인트 {points.length}개 · 단위 nm
        </span>
        <span className="ml-auto font-mono text-[11.5px] font-bold text-slate-light">
          {fmtShort(segments[0].at)} ~ {fmtShort(segments[segments.length - 1].at)}
        </span>
      </div>

      <div className="h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 26, right: 52, bottom: 6, left: 4 }}>
            <CartesianGrid stroke="#EDF2FA" vertical={false} />
            {segments.map((s, i) => (
              <ReferenceArea
                key={`bg-${s.key}`}
                x1={s.start}
                x2={s.end}
                fill={focus.has(s.waferNo) ? 'rgba(30,95,194,.075)' : i % 2 ? 'rgba(15,42,92,.022)' : 'transparent'}
                stroke="none"
              />
            ))}
            {/* 웨이퍼 경계 구분선 + W# 라벨 */}
            {segments.map((s) => (
              <ReferenceLine
                key={`bd-${s.key}`}
                x={s.start}
                stroke="#CBD5E1"
                strokeDasharray="3 3"
                label={{
                  value: `W${s.waferNo}`,
                  position: 'top',
                  fill: focus.has(s.waferNo) ? '#1E5FC2' : '#64748B',
                  fontSize: 11.5,
                  fontWeight: 800,
                  fontFamily: MONO,
                }}
              />
            ))}
            {/* 알람 시점 — 세로 점선 + ALM-* 배지 (getAlarms() occurred_at) */}
            {markers.map((m) => (
              <ReferenceLine
                key={`alm-${m.ms}`}
                x={m.ms}
                stroke={m.judgement === 'OOS' ? '#DC2626' : '#D97706'}
                strokeWidth={1.5}
                strokeDasharray="5 4"
                label={{
                  value: m.ids.join(' · '),
                  position: 'insideBottom',
                  fill: m.judgement === 'OOS' ? '#DC2626' : '#D97706',
                  fontSize: 10,
                  fontWeight: 800,
                  fontFamily: MONO,
                }}
              />
            ))}
            {/* 한계선 5개 상시 표시 */}
            {limits.map((l) => {
              const st = REF_STYLE[l.label] ?? REF_STYLE.TARGET
              return (
                <ReferenceLine
                  key={l.label}
                  y={l.value}
                  stroke={st.color}
                  strokeWidth={st.width}
                  strokeDasharray={st.dash}
                  label={{
                    value: `${l.label} ${l.value}`,
                    position: 'right',
                    fill: st.color === '#94A3B8' ? '#64748B' : st.color,
                    fontSize: 10.5,
                    fontWeight: 800,
                    fontFamily: MONO,
                  }}
                />
              )
            })}
            <XAxis
              dataKey="x"
              type="number"
              domain={[x0 - padX, x1]}
              ticks={segments.map((s) => s.start)}
              tickFormatter={(v) => tickLabel.get(v) ?? ''}
              axisLine={false}
              tickLine={false}
              tick={{ fill: '#475569', fontSize: 11, fontWeight: 700, fontFamily: MONO }}
            />
            <YAxis
              type="number"
              domain={[lo - padY, hi + padY]}
              ticks={[...limitValues].sort((a, b) => a - b)}
              tickFormatter={(v) => String(v)}
              axisLine={false}
              tickLine={false}
              width={46}
              tick={{ fill: '#64748B', fontSize: 11, fontWeight: 700, fontFamily: MONO }}
            />
            <Tooltip content={<TraceTooltip lim={lim} />} />
            {segments.map((s) => (
              <Line
                key={s.key}
                dataKey={s.key}
                stroke="#1E5FC2"
                strokeWidth={focus.has(s.waferNo) ? 3 : 1.8}
                strokeOpacity={focus.size === 0 || focus.has(s.waferNo) ? 1 : 0.45}
                connectNulls={false}
                dot={dotFor(s.key, lim, focus.has(s.waferNo))}
                activeDot={false}
                isAnimationActive={false}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11.5px] font-bold text-slate">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-oos" />
          OOS (USL {lim.USL} 초과)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-ooc" />
          OOC (UCL {lim.UCL} 초과)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-brand" />
          정상 범위
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-[20px] border-t-2 border-dashed border-oos" />
          알람 시점
        </span>
        {focusedWafers.length > 0 && (
          <span className="font-mono text-brand">
            강조: {focusedWafers.map((s) => `W${s.waferNo}`).join(' · ')} (알람 진입 웨이퍼)
          </span>
        )}
      </div>

      {missing.length > 0 && (
        <div className="mt-2 rounded-lg border border-[#FDE68A] bg-ooc-soft px-3 py-2 text-[11.5px] font-bold text-ooc">
          포인트 실측 미제공 — {missing.map((m) => `W${m.waferNo}/${m.step}`).join(', ')} 는{' '}
          <span className="font-mono">points: null</span> 이라 파형을 그리지 않았습니다.
        </div>
      )}

      <div className="mt-1.5 text-[11px] font-semibold text-slate-light">
        x축 경계(W# 위치)는 웨이퍼 occurred_at 실측값이다. 웨이퍼 구간 내부의 포인트 간격은 균등 배치한 렌더링
        가정으로, 포인트별 measured_at 실측은 미확보다.
      </div>
    </div>
  )
}

export default TraceChart
