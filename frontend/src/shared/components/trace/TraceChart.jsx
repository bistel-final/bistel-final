import { useState } from 'react'
import {
  Brush,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
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
const POINT_COLOR = '#e03131' // 측정 시각별 실측 점 — 눈에 바로 걸리는 빨강
// 한계선은 이탈 구간을 면으로 칠하지 않고 색이 있는 가로 점선으로만 나타낸다.
// USL·LSL = spec(적색), UCL·LCL = control(황색), TGT = 목표(청색).
// 상·하한은 같은 색에서 dash 길이(긴=상한 · 짧은=하한)로 구분한다.
const LIMIT_STYLE = {
  USL: { color: '#c2384a', dash: '9 5', opacity: 1, width: 1.6 },
  LSL: { color: '#c2384a', dash: '3 5', opacity: 1, width: 1.6 },
  UCL: { color: '#c07a12', dash: '9 5', opacity: 1, width: 1.5 },
  LCL: { color: '#c07a12', dash: '3 5', opacity: 1, width: 1.5 },
  TARGET: { color: '#2f5fa8', dash: '2 6', opacity: 1, width: 1.4 },
}
const limitColor = (styleLabel) => (LIMIT_STYLE[styleLabel] ?? LIMIT_STYLE.TARGET).color
// Y축 라벨 최소 간격(축 범위 대비). recharts YAxis 기본 interval='preserveEnd'는 라벨이
// 겹치면 조용히 버린다 — 측정값이 넓게 퍼진 알람에서 UCL·LCL 라벨이 사라지던 원인이다.
// interval={0}으로 전부 그리게 하고, 글씨가 서로 위에 찍힐 만큼 가까운 한계만
// 여기서 하나로 정리한다 — 그 경우에도 점선 자체는 둘 다 그대로 그린다.
const TICK_MIN_GAP = 0.055
const LIMIT_RANK = { USL: 3, LSL: 3, UCL: 2, LCL: 2, TARGET: 1 }
const limitRank = (line) => LIMIT_RANK[line.styleLabel] ?? 0

function axisLimitLines(lines, yDomain) {
  if (!yDomain.every(Number.isFinite)) return lines
  const span = yDomain[1] - yDomain[0]
  if (!(span > 0)) return lines
  const kept = []
  for (const line of [...lines].sort((a, b) => limitRank(b) - limitRank(a))) {
    if (kept.some((item) => Math.abs(item.value - line.value) < span * TICK_MIN_GAP)) continue
    kept.push(line)
  }
  return kept.sort((a, b) => a.value - b.value)
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
      fill={line ? limitColor(line.styleLabel) : '#64748b'}
    >
      {label}
    </text>
  )
}
const axisValue = (value) => Number(Number(value).toFixed(3)).toString()

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

function PointDot({ cx, cy, payload, stroke, highlightWaferNo, fill = '#fff' }) {
  if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null
  const highlighted = Number(payload?.wafer_no) === Number(highlightWaferNo)
  const focusRing = highlighted
    ? <circle cx={cx} cy={cy} r={7} fill="none" stroke={SINGLE_COLOR} strokeWidth={1.5} opacity={0.5} />
    : null
  return <g>{focusRing}<circle cx={cx} cy={cy} r={3.2} fill={fill} stroke={fill === '#fff' ? stroke ?? SINGLE_COLOR : fill} strokeWidth={1.5} /></g>
}

export default function TraceChart({ wafers = [], limit = null, height = 300, syncId = 'incident-trace', highlightWaferNo = null, viewMode = 'context' }) {
  const [highlighted, setHighlighted] = useState(null)
  const selectedView = viewMode === 'selected'
  const { rows, series } = selectedView ? selectedWaferChartModel(wafers[0]) : traceChartModel(wafers)
  const yDomain = traceYAxisDomain(wafers, limit, { includeAllLimits: true })
  const displayedLimits = visibleLimitLines(limit)
  const axisLimits = axisLimitLines(displayedLimits, yDomain)
  const ticks = yAxisTicks(yDomain, axisLimits)
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
          <YAxis domain={yDomain} allowDataOverflow ticks={ticks} interval={0} tick={<YAxisTick lines={axisLimits} />} width={78} />
          <Tooltip content={<TraceTooltip limit={limit} />} />
          <Legend
            wrapperStyle={{ fontSize: 10 }}
            onMouseEnter={(entry) => setHighlighted(entry.dataKey ?? entry.value)}
            onMouseLeave={() => setHighlighted(null)}
          />
          {displayedLimits.map((line) => {
            const style = LIMIT_STYLE[line.styleLabel] ?? LIMIT_STYLE.TARGET
            return (
              <ReferenceLine
                key={line.label}
                y={line.value}
                stroke={style.color}
                strokeWidth={style.width}
                strokeOpacity={style.opacity}
                strokeDasharray={style.dash}
              />
            )
          })}
          {series.map((item, index) => {
            const color = selectedView ? SINGLE_COLOR : COLORS[index % COLORS.length]
            return <Line key={item.key} type="linear" dataKey={item.key} name={item.label} stroke={color} strokeWidth={selectedView ? 2.8 : 2} strokeOpacity={highlighted && highlighted !== item.key ? 0.2 : 1} connectNulls={false} dot={(props) => <PointDot {...props} stroke={color} fill={selectedView ? POINT_COLOR : '#fff'} highlightWaferNo={selectedView ? null : highlightWaferNo} />} activeDot={{ r: 5, fill: selectedView ? POINT_COLOR : color, stroke: '#fff' }} isAnimationActive={false} />
          })}
          {!selectedView && rows.length > 25 && <Brush dataKey="wafer_label" height={22} stroke="#cbd5e1" travellerWidth={8} />}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
