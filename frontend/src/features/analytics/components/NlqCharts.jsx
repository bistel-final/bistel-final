// 자연어 분석 결과 차트 — 팀 패턴: 차트 라이브러리 미사용.
// 차트 종류·축은 응답 visualization(chart_type·x·y)이 확정한다 — UI 는 그리기만 한다 (FR-D-04).
//
// 발표 스케일 재설계:
//   - line·histogram 은 카드 폭(≈1200px)에 1:1 로 맞춘 viewBox — 글자가 늘어나거나 줄지 않는다
//   - 가로 bar 는 SVG 대신 HTML — 글자는 CSS px 로 고정, 막대는 얇게(9px), 라벨/값은 표처럼 열 정렬
//   - 팔레트: 스틸블루(--color-blue) 단색, 그리드는 cell-line 3줄, 숫자는 mono

const BLUE_HEX = '#2f5fa8' // = --color-blue
const VW = 1200
const VH = 340
const L = 52
const R = VW - 28
const T = 26
const B = VH - 36

const niceMax = (m) => Math.max(4, Math.ceil(m / 4) * 4)

// x 라벨 표기 — ISO 시각은 날짜만, 그 외는 그대로 (최대 12자)
function shortLabel(v) {
  const s = String(v)
  const iso = s.match(/^(\d{4}-\d{2}-\d{2})/)
  return (iso ? iso[1].slice(5) : s).slice(0, 12)
}

// ── 가로 막대 (HTML) — 범주 비교. 라벨이 길고 범주가 많은 데이터 특성상 가로가 정석. ──
// 얇은 막대 + 아주 연한 트랙 + 값 열 정렬. 최댓값 행은 navy 로 한 톤 진하게 — 12행이 같은
// 그림으로 보이지 않게 "어디가 가장 큰가"만 살린다. 축 눈금은 트랙 아래 한 줄.
export function BarChart({ rows, x, y }) {
  const values = rows.map((r) => Number(r[y]) || 0)
  const max = niceMax(Math.max(1, ...values))
  const peak = Math.max(...values)
  return (
    <div className="px-2 pt-3 font-mono">
      <div className="flex flex-col gap-2">
        {rows.map((r, i) => {
          const v = values[i]
          const pct = (v / max) * 100
          const isPeak = v === peak && v > 0
          return (
            <div key={i} className="grid h-[40px] grid-cols-[170px_1fr_80px] items-center gap-5">
              <span className="truncate text-right text-[15px] font-semibold text-g1" title={String(r[x])}>
                {shortLabel(r[x])}
              </span>
              <div className="relative h-[14px] w-full overflow-hidden rounded-full bg-cell-line">
                {v > 0 && (
                  <div
                    className="h-full rounded-full transition-[width] duration-500"
                    style={{ width: `${pct}%`, background: isPeak ? 'var(--color-navy)' : BLUE_HEX }}
                  />
                )}
              </div>
              <span className={`text-right text-[17px] font-bold ${isPeak ? 'text-navy' : 'text-g1'}`}>{v}</span>
            </div>
          )
        })}
      </div>
      {/* 축 눈금 — 트랙 열과 같은 폭에 0 · 중간 · 최대 */}
      <div className="mt-3 grid grid-cols-[170px_1fr_80px] gap-5">
        <span />
        <div className="relative h-5 border-t border-cell-line text-[12.5px] text-g2">
          <span className="absolute left-0 top-1">0</span>
          <span className="absolute left-1/2 top-1 -translate-x-1/2">{max / 2}</span>
          <span className="absolute right-0 top-1">{max}</span>
        </div>
        <span />
      </div>
    </div>
  )
}

// 대시보드와 동일한 3줄 그리드(0·중간·최대) + 하단 축선
function Grid({ max, y }) {
  return (
    <>
      {[0, max / 2, max].map((t) => (
        <g key={t}>
          <line x1={L} y1={y(t)} x2={R} y2={y(t)} stroke="var(--color-cell-line)" />
          <text x={L - 10} y={y(t) + 5} fontSize="14" fill="var(--color-g2)" textAnchor="end">
            {t}
          </text>
        </g>
      ))}
      <line x1={L} y1={B} x2={R} y2={B} stroke="var(--color-line)" />
    </>
  )
}

// ── 추이 (SVG) — 단일 시리즈 라인. 표 탭 정렬과 무관하게 시간축 오름차순으로 그린다. ──
export function LineChart({ rows, x, y }) {
  const ordered = [...rows].sort((a, b) => (String(a[x]) < String(b[x]) ? -1 : 1))
  const values = ordered.map((r) => Number(r[y]) || 0)
  const max = niceMax(Math.max(1, ...values))
  const n = ordered.length
  const px = (i) => (n > 1 ? L + (i * (R - L)) / (n - 1) : (L + R) / 2)
  const py = (v) => B - (v / max) * (B - T)
  const pts = values.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(' ')
  const area = n ? `${L},${B} ${pts} ${px(n - 1).toFixed(1)},${B}` : ''
  const last = n - 1
  const step = Math.max(1, Math.ceil(n / 10))
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="block w-full font-mono" style={{ height: VH }} preserveAspectRatio="xMinYMid meet">
      <Grid max={max} y={py} />
      {n > 0 && <polygon points={area} fill={BLUE_HEX} opacity="0.06" />}
      <polyline points={pts} fill="none" stroke={BLUE_HEX} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
      {values.map((v, i) => (
        <circle key={i} cx={px(i)} cy={py(v)} r={i === last ? 6.5 : 4.5} fill="#fff" stroke={BLUE_HEX} strokeWidth="2.5" />
      ))}
      {n > 0 && (
        <text x={px(last) + 14} y={py(values[last]) + 5} fontSize="16" fontWeight="700" fill="var(--color-navy)" textAnchor="start">
          {values[last]}
        </text>
      )}
      {ordered.map((r, i) =>
        i % step === 0 || i === last ? (
          <text key={i} x={px(i)} y={VH - 12} fontSize="14" fill="var(--color-g2)" textAnchor="middle">
            {shortLabel(r[x])}
          </text>
        ) : null,
      )}
    </svg>
  )
}

// ── 히스토그램 (SVG) — pre-binned(y 지정) 와 raw(y 없음: 10 bin 등간격 집계) 모두 그린다. ──
// bin 계산은 렌더링이지 차트 재판단이 아니다. 최대 bin 은 진하게 — 피크가 한눈에.
export function HistogramChart({ rows, x, y }) {
  let bars
  if (y) {
    bars = rows.map((r) => ({ label: shortLabel(r[x]), value: Number(r[y]) || 0 }))
  } else {
    const values = rows.map((r) => Number(r[x])).filter((v) => Number.isFinite(v))
    if (values.length === 0) return null
    const min = Math.min(...values)
    const maxV = Math.max(...values)
    const binCount = 10
    const width = (maxV - min) / binCount || 1
    const counts = Array.from({ length: binCount }, () => 0)
    values.forEach((v) => {
      const idx = Math.min(binCount - 1, Math.floor((v - min) / width))
      counts[idx] += 1
    })
    bars = counts.map((c, i) => ({ label: `${(min + i * width).toFixed(1)}`, value: c }))
  }

  const max = niceMax(Math.max(1, ...bars.map((b) => b.value)))
  const peak = Math.max(...bars.map((b) => b.value))
  const py = (v) => B - (v / max) * (B - T)
  const gap = 10
  const bw = (R - L - gap * (bars.length - 1)) / bars.length
  const step = Math.max(1, Math.ceil(bars.length / 10))
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="block w-full font-mono" style={{ height: VH }} preserveAspectRatio="xMinYMid meet">
      <Grid max={max} y={py} />
      {bars.map((b, i) => {
        const h = (b.value / max) * (B - T)
        const bx = L + i * (bw + gap)
        const isPeak = b.value === peak && b.value > 0
        return (
          <g key={i}>
            {b.value > 0 && (
              <rect x={bx} y={B - h} width={bw} height={h} rx="3" fill={isPeak ? 'var(--color-navy)' : BLUE_HEX} opacity={isPeak ? 1 : 0.55} />
            )}
            {b.value > 0 && (isPeak || bars.length <= 10) && (
              <text x={bx + bw / 2} y={B - h - 8} fontSize="14" fontWeight="700" fill="var(--color-g1)" textAnchor="middle">
                {b.value}
              </text>
            )}
            {i % step === 0 || i === bars.length - 1 ? (
              <text x={bx + bw / 2} y={VH - 12} fontSize="14" fill="var(--color-g2)" textAnchor="middle">
                {b.label}
              </text>
            ) : null}
          </g>
        )
      })}
    </svg>
  )
}
