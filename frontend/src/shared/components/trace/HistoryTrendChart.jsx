import { Card } from '../ui/Card.jsx'
import { judgeValue, limitLines } from '../../trace/traceModel.js'

const W = 1000
const H = 300
const L = 56
const R = 940
const T = 20
const B = 258

const LIMIT_HEX = { USL: '#dc2626', LSL: '#dc2626', UCL: '#f59e0b', LCL: '#f59e0b', TARGET: '#16a34a' }
const POINT_HEX = { OOS: '#dc2626', OOC: '#f59e0b' }

const KST_MS = 9 * 60 * 60 * 1000
const hhmm = (ms) => new Date(ms + KST_MS).toISOString().slice(11, 16)

function HistoryTrendChart({ wafer, lim, emptyMessage = null }) {
  const points = [...(wafer?.points ?? [])]
    .map((point) => ({ ...point, ms: Date.parse(point.measured_at) }))
    .filter((point) => Number.isFinite(point.ms))
    .sort((a, b) => a.ms - b.ms)
  const limits = limitLines(lim)

  if (points.length === 0) {
    return (
      <div className="flex h-[300px] items-center justify-center rounded-[10px] border-[1.5px] border-dashed border-dash-line text-[12.5px] text-g2">
        {emptyMessage ?? '선택한 알람의 trace 실측이 응답에 없습니다'}
      </div>
    )
  }

  const t0 = points[0].ms
  const t1 = Math.max(points[points.length - 1].ms, t0 + 1)
  const x = (ms) => L + ((ms - t0) / (t1 - t0)) * (R - L)

  const values = points.map((point) => point.value)
  const limitValues = limits.map((line) => line.value)
  let valueMax = Math.max(...values, ...(limitValues.length ? limitValues : [Math.max(...values)]))
  let valueMin = Math.min(...values, ...(limitValues.length ? limitValues : [Math.min(...values)]))
  if (valueMax === valueMin) {
    valueMax += 1
    valueMin -= 1
  }
  const pad = (valueMax - valueMin) * 0.08
  valueMax += pad
  valueMin -= pad
  const y = (value) => B - ((value - valueMin) / (valueMax - valueMin)) * (B - T)

  const bounds = []
  for (let index = 1; index < points.length; index += 1) {
    if (points[index].recipe_step_no !== points[index - 1].recipe_step_no) {
      bounds.push({ ms: points[index].ms, no: points[index].recipe_step_no })
    }
  }

  const polyline = points.map((point) => `${x(point.ms).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ')

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" fontFamily="IBM Plex Mono, monospace">
      {limits.map((line) => (
        <g key={line.label}>
          <line
            x1={L}
            y1={y(line.value)}
            x2={R}
            y2={y(line.value)}
            stroke={LIMIT_HEX[line.label]}
            strokeWidth="1"
            strokeDasharray="5 4"
            opacity="0.85"
          />
          <text x={L - 8} y={y(line.value) + 3} fontSize="9" fill="var(--color-g2)" textAnchor="end">
            {line.value}
          </text>
          <text x={R + 6} y={y(line.value) + 3} fontSize="9" fill={LIMIT_HEX[line.label]}>
            {line.label === 'TARGET' ? 'TGT' : line.label}
          </text>
        </g>
      ))}

      {bounds.map((bound) => (
        <g key={bound.ms}>
          <line x1={x(bound.ms)} y1={T} x2={x(bound.ms)} y2={B} stroke="#cbd5e1" strokeWidth="1" />
          <text x={x(bound.ms) + 5} y={T + 10} fontSize="9.5" fill="var(--color-g2)">
            Step {bound.no}
          </text>
        </g>
      ))}
      <text x={L + 4} y={T + 10} fontSize="9.5" fill="var(--color-g2)">
        Step {points[0].recipe_step_no}
      </text>

      <polyline points={polyline} fill="none" stroke="#2563eb" strokeWidth="2" strokeLinejoin="round" />
      {points.map((point) => {
        const judgement = judgeValue(point.value, lim)
        const outOfLimit = judgement === 'OOS' || judgement === 'OOC'
        return (
          <circle
            key={`${point.ms}-${point.seq_no ?? point.value}`}
            cx={x(point.ms)}
            cy={y(point.value)}
            r={outOfLimit ? 5.5 : 2.6}
            fill={outOfLimit ? POINT_HEX[judgement] : '#fff'}
            stroke={outOfLimit ? POINT_HEX[judgement] : '#2563eb'}
            strokeWidth={outOfLimit ? 2 : 1.4}
          >
            <title>{`${point.recipe_step_name ?? ''} · ${point.value}${lim?.unit ? ` ${lim.unit}` : ''}${outOfLimit ? ` · ${judgement}` : ''}`}</title>
          </circle>
        )
      })}

      <g fontSize="9.5" fill="var(--color-g2)">
        <text x={L} y={H - 14}>
          {hhmm(t0)}
        </text>
        <text x={(L + R) / 2} y={H - 14} textAnchor="middle">
          {hhmm((t0 + t1) / 2)}
        </text>
        <text x={R} y={H - 14} textAnchor="end">
          {hhmm(t1)}
        </text>
      </g>
    </svg>
  )
}

export function HistoryTrendCard({ alarm, wafer, lim, loading, emptyMessage = null, actions = null }) {
  const parameter = alarm?.parameter_id ?? alarm?.sensor_id
  const waferLabel = alarm?.wafer_id ?? (alarm?.wafer_no != null ? `W${alarm.wafer_no}` : null)
  return (
    <Card className="px-5 pb-3 pt-4">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <span className="font-mono text-[14px] font-extrabold text-ink">
          {alarm ? `${parameter ?? 'PARAMETER 미제공'} · ${waferLabel ?? 'WAFER 미제공'} · ${alarm.chamber_id}` : '선택 알람 트렌드'}
        </span>
        <span className="flex items-center gap-2.5">
          <span className="text-[11.5px] text-g2">
            {alarm
              ? `${lim?.parameter_name ?? lim?.sensor_name ?? ''}${lim?.unit ? ` · 단위 ${lim.unit}` : ''}`
              : emptyMessage
                ? '실측 데이터 연결 대기'
                : '행을 선택하면 트렌드가 표시됩니다'}
          </span>
          {alarm ? actions : null}
        </span>
      </div>
      {loading ? (
        <div className="flex h-[300px] items-center justify-center text-[12.5px] text-g2">트렌드를 불러오는 중…</div>
      ) : alarm ? (
        <HistoryTrendChart wafer={wafer} lim={lim} emptyMessage={emptyMessage} />
      ) : emptyMessage ? (
        <div className="flex h-[300px] items-center justify-center rounded-[10px] border-[1.5px] border-dashed border-dash-line px-6 text-center text-[12.5px] text-g2">
          {emptyMessage}
        </div>
      ) : (
        <div className="flex h-[300px] items-center justify-center rounded-[10px] border-[1.5px] border-dashed border-dash-line text-[12.5px] text-g2">
          테이블에서 알람 행을 선택해 주세요
        </div>
      )}
    </Card>
  )
}

export default HistoryTrendChart
