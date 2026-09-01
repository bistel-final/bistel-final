// 대시보드 차트 프리미티브 — SVG 직접 렌더 (팀 패턴: 차트 라이브러리 미사용)
//
// #260 발표 스케일 재설계. 원칙 세 가지:
//   1. 시선의 순서 — 히어로(전체·OOS/OOC 비율) → 메인 차트(추이, 전폭) → 상세(분포·Agent)
//   2. 반복 제거 — 같은 그림 3장(챔버·설비·파라미터)은 탭 1장으로
//   3. 숫자는 크게, 장식은 없이 — 축 12px · 범례 13px · 제목 15px · 히어로 60px
//
// 팔레트는 앱 정체성(navy 사이드바 + blue 포인트) 단색 계열 — 위계는 색상이 아니라 명도로.
//   navy(심각·OOS) > blue(주의·OOC) > sky(3순위) > gray(중립)
// 빨강은 데이터 범주가 아니라 시스템 이상(거부·실패 배지)에만 쓴다.
import { Card } from '../../../shared/components/ui/Card.jsx'

export const OOS_HEX = '#1c3150' // navy — OOS(심각)
export const OOC_HEX = '#2f5fa8' // steel blue — OOC(주의) = --color-blue
export const BLUE_HEX = '#2f5fa8'
export const SKY_HEX = '#a9c0e4' // = --color-tint-blue-line — 3순위 범주
export const GREEN_HEX = '#2f5fa8' // (하위 호환) 대시보드에서 초록은 퇴장 — blue 로 수렴
export const GRAY_HEX = '#94a3b8'

const niceMax = (m) => Math.max(4, Math.ceil(m / 4) * 4)
const MONO = 'IBM Plex Mono, monospace'

// ── 카드 셸 ────────────────────────────────────────────────────────────
// action: 헤더 우측 슬롯(탭 등). note 는 action 이 없을 때 우측 보조 설명.
export function ChartCard({ title, note, action, className = '', children }) {
  return (
    <Card className={`min-w-0 px-5 pb-4 pt-4 ${className}`}>
      <div className="mb-2 flex min-h-[28px] items-center justify-between gap-3 px-0.5">
        <span className="text-[15px] font-bold tracking-[-.01em] text-ink">{title}</span>
        {action ?? (note && <span className="text-[12.5px] text-g2">{note}</span>)}
      </div>
      {children}
    </Card>
  )
}

export function LegendRow({ items, className = '' }) {
  return (
    <div className={`flex items-center gap-5 px-0.5 pt-2 ${className}`}>
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-2 text-[13px] font-semibold text-g1">
          <span className="h-2.5 w-2.5 flex-none rounded-[3px]" style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  )
}

// ── 히어로 비율바 — OOS/OOC 구성 (도넛 대체) ───────────────────────────
export function RatioBar({ oos, ooc }) {
  const total = oos + ooc
  const pOos = total ? Math.round((oos / total) * 100) : 0
  const pOoc = total ? 100 - pOos : 0
  return (
    <div>
      <div className="flex h-[14px] w-full overflow-hidden rounded-full bg-cell-line">
        {oos > 0 && <div className="h-full" style={{ width: `${pOos}%`, background: OOS_HEX }} />}
        {ooc > 0 && <div className="h-full" style={{ width: `${pOoc}%`, background: OOC_HEX }} />}
      </div>
      <div className="mt-2.5 flex items-center justify-between text-[13px]">
        <span className="flex items-center gap-2 text-g1">
          <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: OOS_HEX }} />
          OOS <span className="font-mono font-bold text-navy">{oos}</span>
          <span className="font-mono text-g2">{pOos}%</span>
        </span>
        <span className="flex items-center gap-2 text-g1">
          <span className="font-mono text-g2">{pOoc}%</span>
          <span className="font-mono font-bold text-navy">{ooc}</span> OOC
          <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: OOC_HEX }} />
        </span>
      </div>
    </div>
  )
}

// ── 일자별 추이 — 전폭 메인 차트 ──────────────────────────────────────
// area 한 겹(OOS navy 6%) + 마지막 점 강조·값 라벨. 자연어 분석 line 차트와 같은 문법.
export function TrendLine({ data, height = 300 }) {
  const VW = 1280
  const VH = height
  const L = 48
  const R = VW - 24
  const T = 24
  const B = VH - 36
  const max = niceMax(Math.max(1, ...data.map((d) => Math.max(d.oos, d.ooc))))
  const n = data.length
  const x = (i) => (n > 1 ? L + (i * (R - L)) / (n - 1) : (L + R) / 2)
  const y = (v) => B - (v / max) * (B - T)
  const pts = (k) => data.map((d, i) => `${x(i).toFixed(1)},${y(d[k]).toFixed(1)}`).join(' ')
  const last = n - 1
  const area = n ? `${L},${B} ${pts('oos')} ${x(last).toFixed(1)},${B}` : ''
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="block w-full font-mono" style={{ height }}>
      {[0, max / 2, max].map((t) => (
        <g key={t}>
          <line x1={L} y1={y(t)} x2={R} y2={y(t)} stroke="var(--color-cell-line)" />
          <text x={L - 10} y={y(t) + 4} fontSize="12" fill="var(--color-g2)" textAnchor="end">
            {t}
          </text>
        </g>
      ))}
      <line x1={L} y1={B} x2={R} y2={B} stroke="var(--color-line)" />
      {n > 0 && <polygon points={area} fill={OOS_HEX} opacity="0.06" />}
      <polyline points={pts('ooc')} fill="none" stroke={OOC_HEX} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
      <polyline points={pts('oos')} fill="none" stroke={OOS_HEX} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
      {data.map((d, i) => (
        <g key={d.label}>
          <circle cx={x(i)} cy={y(d.ooc)} r={i === last ? 5.5 : 4} fill="#fff" stroke={OOC_HEX} strokeWidth="2.5" />
          <circle cx={x(i)} cy={y(d.oos)} r={i === last ? 5.5 : 4} fill="#fff" stroke={OOS_HEX} strokeWidth="2.5" />
          <text x={x(i)} y={VH - 12} fontSize="12" fill="var(--color-g2)" textAnchor="middle">
            {d.label}
          </text>
        </g>
      ))}
      {n > 0 && (
        <>
          <text x={x(last) + 12} y={y(data[last].oos) + 4} fontSize="13" fontWeight="700" fill={OOS_HEX} textAnchor="start" fontFamily={MONO}>
            {data[last].oos}
          </text>
          <text x={x(last) + 12} y={y(data[last].ooc) + 4} fontSize="13" fontWeight="700" fill={OOC_HEX} textAnchor="start" fontFamily={MONO}>
            {data[last].ooc}
          </text>
        </>
      )}
    </svg>
  )
}

// ── 누적 막대 — 챔버·설비·파라미터 분포 (탭으로 전환) ────────────────
export function StackBars({ data, height = 300 }) {
  const VW = 640
  const VH = height
  const L = 44
  const R = VW - 16
  const T = 26
  const B = VH - 34
  const max = niceMax(Math.max(1, ...data.map((d) => d.oos + d.ooc)))
  const y = (v) => B - (v / max) * (B - T)
  const n = data.length || 1
  const slot = (R - L) / n
  const bw = Math.max(18, Math.min(56, slot * 0.46))
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="block w-full font-mono" style={{ height }}>
      {[0, max / 2, max].map((t) => (
        <g key={t}>
          <line x1={L} y1={y(t)} x2={R} y2={y(t)} stroke="var(--color-cell-line)" />
          <text x={L - 8} y={y(t) + 4} fontSize="12" fill="var(--color-g2)" textAnchor="end">
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
            {d.ooc > 0 && <rect x={cx - bw / 2} y={yTop} width={bw} height={yOos - yTop} fill={OOC_HEX} rx="3" />}
            {total > 0 && (
              <text x={cx} y={yTop - 7} fontSize="12" fontWeight="700" fill="var(--color-g1)" textAnchor="middle">
                {total}
              </text>
            )}
            <text x={cx} y={VH - 12} fontSize="11.5" fill="var(--color-g2)" textAnchor="middle">
              {d.label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// ── 단일값 막대 (하위 호환) ────────────────────────────────────────────
export function ValueBars({ data, height = 238 }) {
  const VW = 640
  const VH = height
  const L = 44
  const R = VW - 16
  const T = 22
  const B = VH - 30
  const max = niceMax(Math.max(1, ...data.map((d) => d.value)))
  const y = (v) => B - (v / max) * (B - T)
  const n = data.length || 1
  const slot = (R - L) / n
  const bw = Math.max(20, Math.min(64, slot * 0.5))
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="block w-full font-mono" style={{ height }}>
      {[0, max / 2, max].map((t) => (
        <g key={t}>
          <line x1={L} y1={y(t)} x2={R} y2={y(t)} stroke="var(--color-cell-line)" />
          <text x={L - 8} y={y(t) + 4} fontSize="12" fill="var(--color-g2)" textAnchor="end">
            {t}
          </text>
        </g>
      ))}
      <line x1={L} y1={B} x2={R} y2={B} stroke="var(--color-line)" />
      {data.map((d, i) => {
        const cx = L + slot * i + slot / 2
        return (
          <g key={d.label}>
            {d.value > 0 && <rect x={cx - bw / 2} y={y(d.value)} width={bw} height={B - y(d.value)} fill={d.color} rx="3" />}
            <text x={cx} y={y(d.value) - 7} fontSize="12" fontWeight="700" fill="var(--color-g1)" textAnchor="middle">
              {d.value}
            </text>
            <text x={cx} y={VH - 12} fontSize="12" fill="var(--color-g2)" textAnchor="middle">
              {d.label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// ── 미니 가로바 — 알림 발송 채널 (라벨 | 바 | 값) ──────────────────────
export function MiniBars({ data }) {
  const max = Math.max(1, ...data.map((d) => d.value))
  return (
    <div className="flex flex-col gap-3">
      {data.map((d) => (
        <div key={d.label} className="grid grid-cols-[56px_1fr_28px] items-center gap-3 text-[13px]">
          <span className="font-semibold text-g1">{d.label}</span>
          <div className="h-[10px] overflow-hidden rounded-full bg-cell-line">
            {d.value > 0 && <div className="h-full rounded-full" style={{ width: `${(d.value / max) * 100}%`, background: d.color }} />}
          </div>
          <span className="text-right font-mono font-bold text-navy">{d.value}</span>
        </div>
      ))}
    </div>
  )
}

// ── 컴팩트 도넛 — Agent 현황 (가는 링 + 아래 범례) ──────────────────────
export function Donut({ slices, size = 128, stroke = 14 }) {
  const total = slices.reduce((s, x) => s + x.value, 0)
  const c = size / 2
  const r = c - stroke / 2 - 2
  const C = 2 * Math.PI * r
  let acc = 0
  return (
    <div className="flex flex-col items-center gap-3">
      <svg viewBox={`0 0 ${size} ${size}`} style={{ width: size, height: size }}>
        {total === 0 ? (
          <circle cx={c} cy={c} r={r} fill="none" stroke="var(--color-cell-line)" strokeWidth={stroke} />
        ) : (
          slices
            .filter((s) => s.value > 0)
            .map((s) => {
              const frac = s.value / total
              const el = (
                <circle
                  key={s.label}
                  cx={c}
                  cy={c}
                  r={r}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={stroke}
                  strokeDasharray={`${(frac * C).toFixed(2)} ${C.toFixed(2)}`}
                  transform={`rotate(${(acc * 360 - 90).toFixed(2)} ${c} ${c})`}
                />
              )
              acc += frac
              return el
            })
        )}
        <text x={c} y={c + 3} fontSize="24" fontWeight="800" fill="var(--color-navy)" textAnchor="middle" fontFamily={MONO}>
          {total}
        </text>
        <text x={c} y={c + 21} fontSize="11" fill="var(--color-g2)" textAnchor="middle">
          건
        </text>
      </svg>
      <div className="grid w-full grid-cols-[auto_1fr_auto_auto] items-center gap-x-2.5 gap-y-1.5 text-[12.5px] text-g1">
        {slices.length === 0 && <span className="col-span-4 text-center text-[12px] text-faint">데이터 없음</span>}
        {slices.map((s) => (
          <div key={s.label} className="contents">
            <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: s.color }} />
            <span className="truncate font-semibold">{s.label}</span>
            <span className="font-mono font-bold text-navy">{s.value}</span>
            <span className="w-9 text-right font-mono text-g2">{total ? Math.round((s.value / total) * 100) : 0}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}
