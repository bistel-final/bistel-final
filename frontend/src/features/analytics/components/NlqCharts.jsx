// 자연어 분석 결과 차트 — line·histogram SVG 직접 렌더 (팀 패턴: 차트 라이브러리 미사용)
// 차트 종류·축은 응답 visualization(chart_type·x·y)이 확정한다 — UI는 그리기만 한다 (FR-D-04).
// 라이트 차트 공통: 축라벨 g2 10px · 그리드선 #e2e8f0 · 데이터 blue

const BLUE_HEX = '#2563eb'
const GRID_HEX = '#e2e8f0'
const VW = 640
const VH = 238
const L = 44
const R = VW - 16
const T = 16
const B = VH - 30

const niceMax = (m) => Math.max(4, Math.ceil(m / 4) * 4)

// x 라벨 표기 — ISO 시각은 날짜만, 그 외는 그대로 (최대 10자)
function shortLabel(v) {
  const s = String(v)
  const iso = s.match(/^(\d{4}-\d{2}-\d{2})/)
  return (iso ? iso[1].slice(5) : s).slice(0, 10)
}

function Grid({ max }) {
  const lines = [0.25, 0.5, 0.75, 1]
  return (
    <>
      {lines.map((f) => {
        const gy = B - f * (B - T)
        return (
          <g key={f}>
            <line x1={L} y1={gy} x2={R} y2={gy} stroke={GRID_HEX} strokeWidth="1" />
            <text x={L - 6} y={gy + 3.5} textAnchor="end" fontSize="10" className="fill-g2">
              {Math.round(f * max)}
            </text>
          </g>
        )
      })}
      <line x1={L} y1={B} x2={R} y2={B} stroke="#cbd5e1" strokeWidth="1" />
    </>
  )
}

// 추이 — 단일 시리즈 라인 (x: 시간·범주 축, y: 수치)
// 표 탭의 사용자 정렬 상태와 무관하게 시간축은 항상 x 오름차순으로 그린다
// (ISO 날짜 문자열은 사전순 = 시간순)
export function LineChart({ rows, x, y }) {
  const ordered = [...rows].sort((a, b) => (String(a[x]) < String(b[x]) ? -1 : 1))
  const values = ordered.map((r) => Number(r[y]) || 0)
  const max = niceMax(Math.max(1, ...values))
  const px = (i) => (ordered.length > 1 ? L + (i * (R - L)) / (ordered.length - 1) : (L + R) / 2)
  const py = (v) => B - (v / max) * (B - T)
  const pts = values.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(' ')
  // x 라벨은 겹치지 않게 최대 8개만 고른다
  const step = Math.max(1, Math.ceil(ordered.length / 8))
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="w-full">
      <Grid max={max} />
      <polyline points={pts} fill="none" stroke={BLUE_HEX} strokeWidth="2" />
      {values.map((v, i) => (
        <circle key={i} cx={px(i)} cy={py(v)} r="3" fill={BLUE_HEX} />
      ))}
      {ordered.map((r, i) =>
        i % step === 0 || i === ordered.length - 1 ? (
          <text key={i} x={px(i)} y={B + 14} textAnchor="middle" fontSize="10" className="fill-g2">
            {shortLabel(r[x])}
          </text>
        ) : null,
      )}
    </svg>
  )
}

// 히스토그램 — 세로 막대. 두 형태를 모두 그린다:
//   pre-binned (y 지정): rows 가 이미 (bin 라벨, 빈도)
//   raw (y 없음): x 컬럼의 수치를 10개 등간격 bin 으로 집계해 그린다
//   (bin 계산은 렌더링이지 차트 재판단이 아니다 — 종류·축은 응답이 확정했다)
export function HistogramChart({ rows, x, y }) {
  let bars
  if (y) {
    bars = rows.map((r) => ({ label: shortLabel(r[x]), value: Number(r[y]) || 0 }))
  } else {
    const values = rows.map((r) => Number(r[x])).filter((v) => Number.isFinite(v))
    if (values.length === 0) return null
    const min = Math.min(...values)
    const max = Math.max(...values)
    const binCount = 10
    const width = (max - min) / binCount || 1
    const counts = Array.from({ length: binCount }, () => 0)
    values.forEach((v) => {
      const idx = Math.min(binCount - 1, Math.floor((v - min) / width))
      counts[idx] += 1
    })
    bars = counts.map((c, i) => ({
      label: `${(min + i * width).toFixed(1)}`,
      value: c,
    }))
  }

  const max = niceMax(Math.max(1, ...bars.map((b) => b.value)))
  const gap = 6
  const bw = (R - L - gap * (bars.length - 1)) / bars.length
  const step = Math.max(1, Math.ceil(bars.length / 8))
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="w-full">
      <Grid max={max} />
      {bars.map((b, i) => {
        const h = (b.value / max) * (B - T)
        const bx = L + i * (bw + gap)
        return (
          <g key={i}>
            <rect x={bx} y={B - h} width={bw} height={h} rx="2" fill={BLUE_HEX} opacity="0.85" />
            {i % step === 0 || i === bars.length - 1 ? (
              <text x={bx + bw / 2} y={B + 14} textAnchor="middle" fontSize="10" className="fill-g2">
                {b.label}
              </text>
            ) : null}
          </g>
        )
      })}
    </svg>
  )
}
