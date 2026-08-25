// 대시보드 차트 프리미티브 — 라이트 시안 차트 8장을 SVG 직접 렌더 (팀 패턴: 차트 라이브러리 미사용)
// 라이트 차트 공통: 축라벨 g2 10px · 축선 line · 그리드선 cell-line · 범례 g1
import { Card } from '../../../shared/components/ui/Card.jsx'

export const OOS_HEX = '#dc2626'
export const OOC_HEX = '#f59e0b'
export const BLUE_HEX = '#2563eb'
export const GREEN_HEX = '#16a34a'
export const GRAY_HEX = '#94a3b8'

const VW = 640
const VH = 238
const niceMax = (m) => Math.max(4, Math.ceil(m / 4) * 4)

export function ChartCard({ title, note, children }) {
  return (
    <Card className="min-w-0 px-4 pb-2.5 pt-3.5">
      <div className="mb-1.5 flex items-baseline justify-between px-1">
        <span className="text-[13px] font-bold text-ink">{title}</span>
        {note && <span className="text-[11px] text-g2">{note}</span>}
      </div>
      {children}
    </Card>
  )
}

export function LegendRow({ items }) {
  return (
    <div className="flex items-center gap-3.5 px-1 pt-1">
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-1.5 text-[11px] font-semibold text-g1">
          <span className="h-2 w-2 flex-none rounded-[2px]" style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  )
}

// 일자별 추이 — OOS red / OOC amber 라인
export function TrendLine({ data }) {
  const L = 40
  const R = VW - 16
  const T = 16
  const B = VH - 28
  const max = niceMax(Math.max(1, ...data.map((d) => Math.max(d.oos, d.ooc))))
  const x = (i) => (data.length > 1 ? L + (i * (R - L)) / (data.length - 1) : (L + R) / 2)
  const y = (v) => B - (v / max) * (B - T)
  const pts = (k) => data.map((d, i) => `${x(i).toFixed(1)},${y(d[k]).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="block h-[238px] w-full font-mono">
      {[0, max / 2, max].map((t) => (
        <g key={t}>
          <line x1={L} y1={y(t)} x2={R} y2={y(t)} stroke="var(--color-cell-line)" />
          <text x={L - 6} y={y(t) + 3.5} fontSize="10" fill="var(--color-g2)" textAnchor="end">
            {t}
          </text>
        </g>
      ))}
      <line x1={L} y1={B} x2={R} y2={B} stroke="var(--color-line)" />
      <polyline points={pts('ooc')} fill="none" stroke={OOC_HEX} strokeWidth="2" strokeLinejoin="round" />
      <polyline points={pts('oos')} fill="none" stroke={OOS_HEX} strokeWidth="2" strokeLinejoin="round" />
      {data.map((d, i) => (
        <g key={d.label}>
          <circle cx={x(i)} cy={y(d.ooc)} r="3.5" fill="#fff" stroke={OOC_HEX} strokeWidth="2" />
          <circle cx={x(i)} cy={y(d.oos)} r="3.5" fill="#fff" stroke={OOS_HEX} strokeWidth="2" />
          <text x={x(i)} y={VH - 10} fontSize="10" fill="var(--color-g2)" textAnchor="middle">
            {d.label}
          </text>
        </g>
      ))}
    </svg>
  )
}

// 누적 bar — 하단 OOS red + 상단 OOC amber, 총합 라벨 top
export function StackBars({ data }) {
  const L = 40
  const R = VW - 16
  const T = 22
  const B = VH - 30
  const max = niceMax(Math.max(1, ...data.map((d) => d.oos + d.ooc)))
  const y = (v) => B - (v / max) * (B - T)
  const n = data.length || 1
  const slot = (R - L) / n
  const bw = Math.max(14, Math.min(46, slot * 0.55))
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="block h-[238px] w-full font-mono">
      {[0, max / 2, max].map((t) => (
        <g key={t}>
          <line x1={L} y1={y(t)} x2={R} y2={y(t)} stroke="var(--color-cell-line)" />
          <text x={L - 6} y={y(t) + 3.5} fontSize="10" fill="var(--color-g2)" textAnchor="end">
            {t}
          </text>
        </g>
      ))}
      <line x1={L} y1={B} x2={R} y2={B} stroke="var(--color-line)" />
      {data.map((d, i) => {
        const cx = L + slot * i + slot / 2
        const total = d.oos + d.ooc
        const yOos = y(d.oos)
        const yTop = y(total)
        return (
          <g key={d.label}>
            {d.oos > 0 && <rect x={cx - bw / 2} y={yOos} width={bw} height={B - yOos} fill={OOS_HEX} />}
            {d.ooc > 0 && <rect x={cx - bw / 2} y={yTop} width={bw} height={yOos - yTop} fill={OOC_HEX} rx="2" />}
            {total > 0 && (
              <text x={cx} y={yTop - 5} fontSize="10" fontWeight="700" fill="var(--color-g1)" textAnchor="middle">
                {total}
              </text>
            )}
            <text x={cx} y={VH - 12} fontSize="9" fill="var(--color-g2)" textAnchor="middle">
              {d.label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// 단색 bar — 값 라벨 top (알림 발송)
export function ValueBars({ data }) {
  const L = 40
  const R = VW - 16
  const T = 22
  const B = VH - 30
  const max = niceMax(Math.max(1, ...data.map((d) => d.value)))
  const y = (v) => B - (v / max) * (B - T)
  const n = data.length || 1
  const slot = (R - L) / n
  const bw = Math.max(20, Math.min(64, slot * 0.5))
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="block h-[238px] w-full font-mono">
      {[0, max / 2, max].map((t) => (
        <g key={t}>
          <line x1={L} y1={y(t)} x2={R} y2={y(t)} stroke="var(--color-cell-line)" />
          <text x={L - 6} y={y(t) + 3.5} fontSize="10" fill="var(--color-g2)" textAnchor="end">
            {t}
          </text>
        </g>
      ))}
      <line x1={L} y1={B} x2={R} y2={B} stroke="var(--color-line)" />
      {data.map((d, i) => {
        const cx = L + slot * i + slot / 2
        return (
          <g key={d.label}>
            {d.value > 0 && <rect x={cx - bw / 2} y={y(d.value)} width={bw} height={B - y(d.value)} fill={d.color} rx="2" />}
            <text x={cx} y={y(d.value) - 5} fontSize="10.5" fontWeight="700" fill="var(--color-g1)" textAnchor="middle">
              {d.value}
            </text>
            <text x={cx} y={VH - 12} fontSize="10" fill="var(--color-g2)" textAnchor="middle">
              {d.label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// 도넛 — radius 45~70% 링 + 우측 범례({b}: {c} ({d}%))
export function Donut({ slices }) {
  const total = slices.reduce((s, x) => s + x.value, 0)
  const cx = 120
  const cy = 119
  const r = 69
  const w = 32
  const C = 2 * Math.PI * r
  let acc = 0
  return (
    <div className="flex h-[238px] items-center gap-2">
      <svg viewBox="0 0 240 238" className="h-full flex-none">
        {total === 0 ? (
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--color-cell-line)" strokeWidth={w} />
        ) : (
          slices
            .filter((s) => s.value > 0)
            .map((s) => {
              const frac = s.value / total
              const el = (
                <circle
                  key={s.label}
                  cx={cx}
                  cy={cy}
                  r={r}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={w}
                  strokeDasharray={`${(frac * C).toFixed(2)} ${C.toFixed(2)}`}
                  transform={`rotate(${(acc * 360 - 90).toFixed(2)} ${cx} ${cy})`}
                />
              )
              acc += frac
              return el
            })
        )}
        <text x={cx} y={cy + 2} fontSize="20" fontWeight="800" fill="var(--color-ink)" textAnchor="middle" fontFamily="IBM Plex Mono, monospace">
          {total}
        </text>
        <text x={cx} y={cy + 19} fontSize="9.5" fill="var(--color-g2)" textAnchor="middle">
          건
        </text>
      </svg>
      <div className="flex min-w-0 flex-col gap-2">
        {slices.map((s) => (
          <div key={s.label} className="flex items-center gap-2 text-[11.5px] text-g1">
            <span className="h-2.5 w-2.5 flex-none rounded-[3px]" style={{ background: s.color }} />
            <span className="truncate font-semibold">{s.label}</span>
            <span className="font-mono font-bold text-ink">{s.value}</span>
            <span className="font-mono text-g2">{total ? Math.round((s.value / total) * 100) : 0}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}
