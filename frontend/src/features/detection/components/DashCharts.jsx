// 대시보드 차트 프리미티브 — SVG 직접 렌더 (팀 패턴: 차트 라이브러리 미사용)
//
// #260 발표 스케일 재설계. 원칙 세 가지:
//   1. 시선의 순서 — 히어로(전체·OOS/OOC 비율) → 메인 차트(추이, 전폭) → 상세(분포·Agent)
//   2. 반복 제거 — 같은 그림 3장(챔버·설비·파라미터)은 탭 1장으로
//   3. 숫자는 크게, 장식은 없이 — 축 12px · 범례 13px · 제목 15px · 히어로 60px
//
// 파레트: 판정은 전 화면 공통 토큰(OOS 크림슨 · OOC 앰버), 그 외는 navy 계열 단색 — 위계는 명도로.
//   OOS(규격 이탈) > OOC(관리한계 이탈) 은 색상으로, navy · navy-2 · gray 는 중립 위계로.
// 빨강은 데이터 범주가 아니라 시스템 이상(거부·실패 배지)에만 쓴다.
import { useState } from 'react'
import { Card } from '../../../shared/components/ui/Card.jsx'

// 판정색은 전 화면 공통 토큰 — OOS 크림슨 · OOC 앰버 (index.css --color-oos / --color-ooc).
// 보족 색은 navy 계열: 중립·합계는 navy, 3순위 범주는 연한 navy, 없으면 gray.
export const OOS_HEX = 'var(--color-oos)' // OOS(규격 이탈) — 크림슨
export const OOC_HEX = 'var(--color-ooc)' // OOC(관리한계 이탈) — 앰버
export const OOS_TEXT_HEX = 'var(--color-oos-text)'
export const OOC_TEXT_HEX = 'var(--color-ooc-text)'
export const BLUE_HEX = '#2f5fa8'
export const SKY_HEX = 'var(--color-navy-2)' // 3순위 범주 — navy 램프 L72
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

// ── hover 툴팁 셸 ─────────────────────────────────────────────────────
// SVG 를 직접 그리는 대시보드 차트에 붙이는 HTML 오버레이.
// 카드 모양은 trace 차트 툴팁(shared/components/trace/TraceChart.jsx)과 같은 문법을 쓴다.
// x·y 는 래퍼 기준 커서 픽셀 좌표 — viewBox 좌표가 아니다(아래 pointerAt 참조).
function ChartTooltip({ x, y, width, children }) {
  const half = 82
  const left = width > half * 2 ? Math.min(Math.max(x, half), width - half) : x
  const below = y < 120 // 위로 띄울 공간이 없으면 커서 아래로 뒤집는다
  return (
    <div
      className="pointer-events-none absolute z-10 min-w-[150px] rounded-xl border border-line bg-white px-3.5 py-2.5 shadow-xl"
      style={{ left, top: below ? y + 20 : y - 14, transform: `translate(-50%, ${below ? '0' : '-100%'})` }}
    >
      {children}
    </div>
  )
}

function TooltipRow({ color, label, value }) {
  return (
    <div className="mt-1.5 flex items-center gap-2 text-[12px] text-g1">
      <span className="h-2.5 w-2.5 flex-none rounded-[3px]" style={{ background: color }} />
      <span className="font-semibold">{label}</span>
      <span className="ml-auto font-mono font-bold text-navy">{value}</span>
    </div>
  )
}

// viewBox 는 preserveAspectRatio 기본값(meet)으로 균등 축소되고 남는 축은 여백이 된다.
// 그래서 마우스 좌표 → viewBox 좌표 변환에 실제 배율과 letterbox 여백을 함께 써야 한다.
// px·py 는 툴팁을 놓을 래퍼 기준 커서 좌표, vx 는 데이터 인덱스를 찾을 viewBox 좌표.
function pointerAt(event, VW, VH) {
  const rect = event.currentTarget.getBoundingClientRect()
  if (!rect.width || !rect.height) return null
  const scale = Math.min(rect.width / VW, rect.height / VH)
  const ox = (rect.width - VW * scale) / 2
  const oy = (rect.height - VH * scale) / 2
  const px = event.clientX - rect.left
  const py = event.clientY - rect.top
  return { vx: (px - ox) / scale, px, py, scale, ox, oy, width: rect.width }
}

// ── 일자별 추이 — 전폭 메인 차트 ──────────────────────────────────────
// area 한 겹(OOS navy 6%) + 마지막 점 강조·값 라벨. 자연어 분석 line 차트와 같은 문법.
// hover: 차트 어디에 올려도 가장 가까운 일자를 잡는다(점 위가 아니어도 된다).
//   가이드선·강조점은 해당 일자에 고정되고 툴팁만 커서를 따라간다 — recharts Tooltip 과 같은 거동.
export function TrendLine({ data, height = 300 }) {
  const VW = 1280
  const VH = height
  const L = 48
  const R = VW - 24
  const T = 24
  const B = VH - 36
  const [hover, setHover] = useState(null)
  const max = niceMax(Math.max(1, ...data.map((d) => Math.max(d.oos, d.ooc))))
  const n = data.length
  const x = (i) => (n > 1 ? L + (i * (R - L)) / (n - 1) : (L + R) / 2)
  const y = (v) => B - (v / max) * (B - T)
  const pts = (k) => data.map((d, i) => `${x(i).toFixed(1)},${y(d[k]).toFixed(1)}`).join(' ')
  const last = n - 1

  const handleMove = (event) => {
    if (!n) return
    const at = pointerAt(event, VW, VH)
    if (!at) return
    let i = 0
    for (let k = 1; k < n; k += 1) if (Math.abs(x(k) - at.vx) < Math.abs(x(i) - at.vx)) i = k
    setHover({ i, px: at.px, py: at.py, width: at.width })
  }

  const point = hover ? data[hover.i] : null
  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${VW} ${VH}`}
        className="block w-full font-mono"
        style={{ height }}
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
      >
        {/* hover 판정면 — 선·점이 그려지지 않은 빈 영역에서도 이벤트를 받게 하는 투명 레이어.
            SVG 기본 pointer-events(visiblePainted)는 칠해진 자리에서만 hit test 되므로 필요하다. */}
        <rect x="0" y="0" width={VW} height={VH} fill="transparent" pointerEvents="all" />
        {[0, max / 2, max].map((t) => (
          <g key={t}>
            <line x1={L} y1={y(t)} x2={R} y2={y(t)} stroke="var(--color-cell-line)" />
            <text x={L - 10} y={y(t) + 4} fontSize="12" fill="var(--color-g2)" textAnchor="end">
              {t}
            </text>
          </g>
        ))}
        <line x1={L} y1={B} x2={R} y2={B} stroke="var(--color-line)" />
        {point && (
          <line x1={x(hover.i)} y1={T - 10} x2={x(hover.i)} y2={B} stroke="var(--color-line)" strokeDasharray="4 4" />
        )}
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
        {point && (
          <g>
            <circle cx={x(hover.i)} cy={y(point.ooc)} r="7" fill="#fff" stroke={OOC_HEX} strokeWidth="3" />
            <circle cx={x(hover.i)} cy={y(point.oos)} r="7" fill="#fff" stroke={OOS_HEX} strokeWidth="3" />
          </g>
        )}
        {n > 0 && (
          <>
            <text x={x(last) + 12} y={y(data[last].oos) + 4} fontSize="13" fontWeight="700" fill={OOS_TEXT_HEX} textAnchor="start" fontFamily={MONO}>
              {data[last].oos}
            </text>
            <text x={x(last) + 12} y={y(data[last].ooc) + 4} fontSize="13" fontWeight="700" fill={OOC_TEXT_HEX} textAnchor="start" fontFamily={MONO}>
              {data[last].ooc}
            </text>
          </>
        )}
      </svg>
      {point && (
        <ChartTooltip x={hover.px} y={hover.py} width={hover.width}>
          <div className="font-mono text-[12.5px] font-extrabold text-ink">{point.date ?? point.label}</div>
          <TooltipRow color={OOS_HEX} label="OOS" value={point.oos} />
          <TooltipRow color={OOC_HEX} label="OOC" value={point.ooc} />
          <div className="mt-2 flex items-center gap-2 border-t border-cell-line pt-1.5 text-[12px] text-g2">
            <span className="font-semibold">합계</span>
            <span className="ml-auto font-mono font-bold text-navy">{point.oos + point.ooc}</span>
          </div>
        </ChartTooltip>
      )}
    </div>
  )
}

// ── 누적 막대 — 챔버·설비·파라미터 분포 (탭으로 전환) ────────────────
// 전폭 카드에서 쓰므로 viewBox 폭을 추이 차트(TrendLine)와 맞춘다 — meet 스케일에서
// 폭이 좁으면 좌우가 레터박스 여백으로 남는다.
export function StackBars({ data, height = 300 }) {
  const VW = 1280
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
