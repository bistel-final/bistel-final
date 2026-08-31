import { Card } from '../../../shared/components/ui/Card.jsx'
import { judgeValue, limitLines } from './TraceModel.jsx'

// 선택 알람 트렌드 — 라이트 시안 2번 상단 카드 (SVG 직접 렌더, 300px)
// 시간축 line #2563eb 2px · 알람(한계선 이탈) 포인트만 확대 심볼 + 시맨틱 색
// markLine: USL/LSL red · UCL/LCL amber · TGT green 점선 + 라벨 · 스텝 경계 세로선 + "Step n"
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
    .map((p) => ({ ...p, ms: Date.parse(p.measured_at) }))
    .filter((p) => Number.isFinite(p.ms))
    .sort((a, b) => a.ms - b.ms)
  const limits = limitLines(lim)

  if (points.length === 0)
    return (
      <div className="flex h-[300px] items-center justify-center rounded-[10px] border-[1.5px] border-dashed border-dash-line text-[12.5px] text-g2">
        {emptyMessage ?? '선택한 알람의 trace 실측이 응답에 없습니다'}
      </div>
    )

  const t0 = points[0].ms
  const t1 = Math.max(points[points.length - 1].ms, t0 + 1)
  const x = (ms) => L + ((ms - t0) / (t1 - t0)) * (R - L)

  const values = points.map((p) => p.value)
  const lv = limits.map((l) => l.value)
  let vMax = Math.max(...values, ...(lv.length ? lv : [Math.max(...values)]))
  let vMin = Math.min(...values, ...(lv.length ? lv : [Math.min(...values)]))
  if (vMax === vMin) {
    vMax += 1
    vMin -= 1
  }
  const pad = (vMax - vMin) * 0.08
  vMax += pad
  vMin -= pad
  const y = (v) => B - ((v - vMin) / (vMax - vMin)) * (B - T)

  // 스텝 경계 — recipe_step_no 가 바뀌는 지점
  const bounds = []
  for (let i = 1; i < points.length; i += 1)
    if (points[i].recipe_step_no !== points[i - 1].recipe_step_no)
      bounds.push({ ms: points[i].ms, no: points[i].recipe_step_no })

  const poly = points.map((p) => `${x(p.ms).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ')

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" fontFamily="IBM Plex Mono, monospace">
      {/* markLine 5종 — 그 센서의 한계선 값으로만 그린다 */}
      {limits.map((l) => (
        <g key={l.label}>
          <line x1={L} y1={y(l.value)} x2={R} y2={y(l.value)} stroke={LIMIT_HEX[l.label]} strokeWidth="1" strokeDasharray="5 4" opacity="0.85" />
          <text x={L - 8} y={y(l.value) + 3} fontSize="9" fill="var(--color-g2)" textAnchor="end">
            {l.value}
          </text>
          <text x={R + 6} y={y(l.value) + 3} fontSize="9" fill={LIMIT_HEX[l.label]}>
            {l.label === 'TARGET' ? 'TGT' : l.label}
          </text>
        </g>
      ))}

      {/* 스텝 경계 세로선 + Step n 라벨 */}
      {bounds.map((b) => (
        <g key={b.ms}>
          <line x1={x(b.ms)} y1={T} x2={x(b.ms)} y2={B} stroke="#cbd5e1" strokeWidth="1" />
          <text x={x(b.ms) + 5} y={T + 10} fontSize="9.5" fill="var(--color-g2)">
            Step {b.no}
          </text>
        </g>
      ))}
      {points.length > 0 && (
        <text x={L + 4} y={T + 10} fontSize="9.5" fill="var(--color-g2)">
          Step {points[0].recipe_step_no}
        </text>
      )}

      <polyline points={poly} fill="none" stroke="#2563eb" strokeWidth="2" strokeLinejoin="round" />
      {points.map((p) => {
        const j = judgeValue(p.value, lim)
        const out = j === 'OOS' || j === 'OOC'
        return (
          <circle
            key={`${p.ms}-${p.seq_no ?? p.value}`}
            cx={x(p.ms)}
            cy={y(p.value)}
            r={out ? 5.5 : 2.6}
            fill={out ? POINT_HEX[j] : '#fff'}
            stroke={out ? POINT_HEX[j] : '#2563eb'}
            strokeWidth={out ? 2 : 1.4}
          >
            <title>{`${p.recipe_step_name ?? ''} · ${p.value}${lim?.unit ? ` ${lim.unit}` : ''}${out ? ` · ${j}` : ''}`}</title>
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

// 트렌드 카드 래퍼 — 제목 `PARAM · WAFER · EQP-CH`
// actions: 알람이 선택된 동안에만 헤더 우측에 얹는 버튼(예: 분석 실행) — V5-A-3.4
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
