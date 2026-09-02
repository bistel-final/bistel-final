import { useState } from 'react'
import {
  Brush,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  formatMeasuredAt,
  judgeValue,
  limitLines,
  selectedWaferChartModel,
  traceChartModel,
  traceYAxisDomain,
} from '../../trace/traceModel.js'

// 색 예산은 하나뿐이다 — 이상 신호(OOS·OOC)에만 쓴다.
// WAFER 계열은 색상(hue)을 바꾸지 않고 navy→blue 한 계열의 명도 ramp로 구분한다.
// 여러 색상을 쓰면 계열 구분과 이상 신호가 같은 무게로 경쟁해 화면이 산만해진다.
const COLORS = ['#c7d9e8', '#b4cbde', '#a0bdd3', '#8cadc7', '#789db9', '#678dab', '#587d9c', '#496d8c']
const SINGLE_COLOR = '#47769d' // 회사 청색을 낮은 채도로 쓰되 한계선보다 분명한 단일 wafer 실측선
const GRID_COLOR = '#eef2f7' // --color-cell-line
// 한계선은 항상 떠 있는 기준선이므로 중립 회색으로 물러난다.
// 색이 아니라 명도(spec > control > target)와 dash(긴=상한 · 짧은=하한)로 읽는다.
const LIMIT_STYLE = {
  USL: { color: '#5f6d7c', dash: '8 5', opacity: 0.96, width: 1.55 },
  LSL: { color: '#5f6d7c', dash: '3 5', opacity: 0.96, width: 1.55 },
  UCL: { color: '#9ca8b5', dash: '8 5', opacity: 0.84, width: 1.15 },
  LCL: { color: '#9ca8b5', dash: '3 5', opacity: 0.84, width: 1.15 },
  TARGET: { color: '#b8c1cb', dash: '2 6', opacity: 0.92, width: 1 },
}

function visibleLimitLines(limit) {
  const merged = new Map()
  for (const line of limitLines(limit)) {
    const key = String(line.value)
    const existing = merged.get(key)
    if (!existing) {
      merged.set(key, { ...line, styleLabel: line.label })
      continue
    }
    const styleLabel = ['USL', 'LSL'].includes(existing.styleLabel)
      ? existing.styleLabel
      : ['USL', 'LSL'].includes(line.label)
        ? line.label
        : existing.styleLabel
    merged.set(key, { ...existing, label: `${existing.label}/${line.label}`, styleLabel })
  }
  return [...merged.values()]
}

function yAxisTicks(yDomain, lines) {
  if (!yDomain.every(Number.isFinite)) return undefined
  const [minimum, maximum] = yDomain
  const span = maximum - minimum
  const regular = Array.from({ length: 5 }, (_, index) => minimum + (span * index) / 4)
    .filter((tick) => !lines.some((line) => Math.abs(tick - line.value) < span * 0.055))
  return [...new Set([...regular, ...lines.map((line) => line.value)])].sort((a, b) => a - b)
}

function YAxisTick({ x, y, payload, lines }) {
  const line = lines.find((item) => item.value === payload.value)
  const label = line
    ? `${line.label === 'TARGET' ? 'TGT' : line.label} ${axisValue(payload.value)}`
    : Number(payload.value.toFixed(1)).toString()
  return (
    <text
      x={x - 7}
      y={y + 4}
      textAnchor="end"
      fontSize={line ? 11 : 10}
      fontWeight={line ? 700 : 400}
      fill={line ? '#4f5d6c' : '#64748b'}
    >
      {label}
    </text>
  )
}
const axisValue = (value) => Number(Number(value).toFixed(3)).toString()

function limitAreas(limit, yDomain) {
  if (!limit || !yDomain.every(Number.isFinite)) return []
  const [domainMin, domainMax] = yDomain
  const areas = []
  const addArea = (key, lower, upper, label, fill, labelColor) => {
    if (!Number.isFinite(lower) || !Number.isFinite(upper)) return
    const y1 = Math.max(domainMin, Math.min(lower, upper))
    const y2 = Math.min(domainMax, Math.max(lower, upper))
    if (y2 <= y1) return
    const showLabel = y2 - y1 >= (domainMax - domainMin) * 0.08
    areas.push({ key, y1, y2, label, fill, labelColor, showLabel })
  }

  addArea('UPPER_OOS', limit.spec_upper, domainMax, 'OOS 영역 · USL 초과', 'var(--color-trace-oos-zone)', 'var(--color-trace-oos)')
  addArea('UPPER_OOC', limit.ctrl_upper, limit.spec_upper, 'OOC 영역 · UCL~USL', 'var(--color-trace-ooc-zone)', 'var(--color-trace-ooc)')
  addArea('LOWER_OOC', limit.spec_lower, limit.ctrl_lower, 'OOC 영역 · LSL~LCL', 'var(--color-trace-ooc-zone)', 'var(--color-trace-ooc)')
  addArea('LOWER_OOS', domainMin, limit.spec_lower, 'OOS 영역 · LSL 미만', 'var(--color-trace-oos-zone)', 'var(--color-trace-oos)')
  return areas
}

const limitDifference = (value, limit, judgement) => {
  if (!Number.isFinite(value) || !limit) return null
  const candidates = judgement === 'OOS'
    ? [['USL', limit.spec_upper], ['LSL', limit.spec_lower]]
    : judgement === 'OOC'
      ? [['UCL', limit.ctrl_upper], ['LCL', limit.ctrl_lower]]
      : [['TARGET', limit.target]]
  const available = candidates.filter(([, boundary]) => Number.isFinite(boundary))
  if (!available.length) return null
  const [label, boundary] = available.reduce((best, item) =>
    Math.abs(value - item[1]) < Math.abs(value - best[1]) ? item : best)
  const delta = value - boundary
  return `${label} 대비 ${delta >= 0 ? '+' : ''}${delta.toFixed(3)}`
}

function TraceTooltip({ active, payload, limit }) {
  if (!active || !payload?.length) return null
  return (
    <div className="min-w-[260px] max-w-[400px] rounded-xl border border-line bg-white px-4 py-3 shadow-xl">
      {payload.map((entry) => {
        const point = entry.payload?.[`${entry.dataKey}:meta`]
        const difference = limitDifference(entry.value, limit, judgeValue(entry.value, limit))
        const measuredTime = formatMeasuredAt(point?.measured_at)
        return (
          <div key={entry.dataKey} className="mb-2 last:mb-0">
            <div className="font-mono text-[12.5px] font-extrabold" style={{ color: entry.color }}>
              {entry.payload?.wafer_label} · {entry.name ?? entry.dataKey}
            </div>
            <div className="mt-1 text-[12px] leading-5 text-g1">
              {limit?.sensor_name ?? point?.sensor_name ?? '센서명 미제공'} · seq {point?.seq_no ?? '—'}
              {' · '}{point?.recipe_step_name ?? `Step ${point?.recipe_step_no ?? '—'}`}
            </div>
            {difference && <div className="text-[11.5px] font-semibold leading-5 text-g1">{difference}</div>}
            {measuredTime && <div className="text-[11.5px] leading-5 text-g2">{measuredTime}</div>}
          </div>
        )
      })}
    </div>
  )
}

function PointDot({ cx, cy, payload, stroke, highlightWaferNo }) {
  if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null
  const highlighted = Number(payload?.wafer_no) === Number(highlightWaferNo)
  const focusRing = highlighted
    ? <circle cx={cx} cy={cy} r={7} fill="none" stroke={SINGLE_COLOR} strokeWidth={1.5} opacity={0.5} />
    : null
  return <g>{focusRing}<circle cx={cx} cy={cy} r={3} fill="#fff" stroke={stroke ?? SINGLE_COLOR} strokeWidth={1.5} /></g>
}

export default function TraceChart({ wafers = [], limit = null, height = 300, syncId = 'incident-trace', highlightWaferNo = null, viewMode = 'context' }) {
  const [highlighted, setHighlighted] = useState(null)
  const selectedView = viewMode === 'selected'
  const { rows, series } = selectedView ? selectedWaferChartModel(wafers[0]) : traceChartModel(wafers)
  const yDomain = traceYAxisDomain(wafers, limit)
  const thresholdAreas = selectedView ? limitAreas(limit, yDomain) : []
  const displayedLimits = visibleLimitLines(limit)
  const ticks = yAxisTicks(yDomain, displayedLimits)
  if (!rows.length) return <div className="flex h-[300px] items-center justify-center text-[12px] text-g2">trace 실측 데이터가 없습니다.</div>
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} syncId={syncId} margin={{ top: 16, right: 24, left: 4, bottom: 8 }}>
          <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" />
          <XAxis
            dataKey={selectedView ? 'point_label' : 'wafer_label'}
            interval={0}
            tick={{ fontSize: 10 }}
            label={{ value: selectedView ? '측정 시각' : 'WAFER', position: 'insideBottomRight', offset: -4, fontSize: 10 }}
          />
          <YAxis domain={yDomain} allowDataOverflow ticks={ticks} tick={<YAxisTick lines={displayedLimits} />} width={78} />
          <Tooltip content={<TraceTooltip limit={limit} />} />
          <Legend
            wrapperStyle={{ fontSize: 10 }}
            onMouseEnter={(entry) => setHighlighted(entry.dataKey ?? entry.value)}
            onMouseLeave={() => setHighlighted(null)}
          />
          {thresholdAreas.map((area) => (
            <ReferenceArea
              key={area.key}
              y1={area.y1}
              y2={area.y2}
              fill={area.fill}
              fillOpacity={0.78}
              ifOverflow="hidden"
              label={area.showLabel ? { value: area.label, position: 'insideTopLeft', fontSize: 9, fontWeight: 700, fill: area.labelColor } : false}
            />
          ))}
          {displayedLimits.map((line) => {
            const style = LIMIT_STYLE[line.styleLabel] ?? LIMIT_STYLE.TARGET
            return <ReferenceLine key={line.label} y={line.value} stroke={style.color} strokeWidth={style.width} strokeOpacity={style.opacity} strokeDasharray={style.dash} />
          })}
          {series.map((item, index) => {
            const color = selectedView ? SINGLE_COLOR : COLORS[index % COLORS.length]
            return <Line key={item.key} type="linear" dataKey={item.key} name={item.label} stroke={color} strokeWidth={selectedView ? 2.8 : 2} strokeOpacity={highlighted && highlighted !== item.key ? 0.2 : 1} connectNulls={false} dot={(props) => <PointDot {...props} stroke={color} highlightWaferNo={selectedView ? null : highlightWaferNo} />} activeDot={{ r: 5 }} isAnimationActive={false} />
          })}
          {!selectedView && rows.length > 25 && <Brush dataKey="wafer_label" height={22} stroke="#cbd5e1" travellerWidth={8} />}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
