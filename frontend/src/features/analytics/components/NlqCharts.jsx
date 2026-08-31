// 자연어 분석 결과 차트 — line·histogram SVG 직접 렌더 (팀 패턴: 차트 라이브러리 미사용)
// 차트 종류·축은 응답 visualization(chart_type·x·y)이 확정한다 — UI는 그리기만 한다 (FR-D-04).
// 시각 문법은 대시보드 차트(DashCharts)와 동일하게 맞춘다:
//   그리드 cell-line 3줄(0·중간·최대) + line 축선 · 축라벨 g2 10px · font-mono
//   점 흰 채움 + 색 테두리 2px · 값 라벨 700 g1 · 막대 rx 2 · 데이터 blue

const BLUE_HEX = '#2563eb'
const VW = 640
const VH = 300
const L = 44
const R = VW - 16
const T = 22
const B = VH - 30

const niceMax = (m) => Math.max(4, Math.ceil(m / 4) * 4)

// x 라벨 표기 — ISO 시각은 날짜만, 그 외는 그대로 (최대 10자)
function shortLabel(v) {
  const s = String(v)
  const iso = s.match(/^(\d{4}-\d{2}-\d{2})/)
  return (iso ? iso[1].slice(5) : s).slice(0, 10)
}

// 범주 비교 — 가로 막대 (라벨이 길고 범주가 많은 데이터 특성상 가로가 정석).
// 대시보드 문법을 가로 방향으로: 세로 그리드 3줄 + 축선 · rx 2 · 값 라벨 700·g1.
// 행 수에 따라 높이가 자라는 동적 viewBox — 12행이어도 리듬이 유지된다.
// 값 라벨은 긴 막대면 안쪽 흰 글씨, 짧으면 바깥 — 오른쪽 들옍날옍을 줄인다.
const BAR_LABEL_W = 118
const ROW_H = 27
export function BarChart({ rows, x, y }) {
  const values = rows.map((r) => Number(r[y]) || 0)
  const max = niceMax(Math.max(1, ...values))
  const height = 14 + rows.length * ROW_H + 22
  const left = BAR_LABEL_W
  const right = VW - 44
  const gx = (v) => left + (v / max) * (right - left)
  const top = 14
  const baseY = top + rows.length * ROW_H
  return (
    <svg viewBox={`0 0 ${VW} ${height}`} className="block w-full font-mono">
      {[0, max / 2, max].map((t) => (
        <g key={t}>
          <line x1={gx(t)} y1={top - 4} x2={gx(t)} y2={baseY} stroke="var(--color-cell-line)" />
          <text x={gx(t)} y={baseY + 14} fontSize="10" fill="var(--color-g2)" textAnchor="middle">
            {t}
          </text>
        </g>
      ))}
      <line x1={left} y1={top - 4} x2={left} y2={baseY} stroke="var(--color-line)" />
      {rows.map((r, i) => {
        const v = values[i]
        const cy = top + i * ROW_H + ROW_H / 2
        const bw = Math.max(v > 0 ? 2 : 0, gx(v) - left)
        const inside = bw >= 44
        return (
          <g key={i}>
            <text
              x={left - 8}
              y={cy + 3.5}
              fontSize="10.5"
              fontWeight="600"
              fill="var(--color-g1)"
              textAnchor="end"
            >
              {shortLabel(r[x])}
            </text>
            {v > 0 && <rect x={left} y={cy - 6.5} width={bw} height={13} rx="2" fill={BLUE_HEX} />}
            <text
              x={inside ? left + bw - 7 : left + bw + 7}
              y={cy + 3.5}
              fontSize="10.5"
              fontWeight="700"
              fill={inside ? '#fff' : 'var(--color-g1)'}
              textAnchor={inside ? 'end' : 'start'}
            >
              {v}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// 대시보드와 동일한 3줄 그리드(0·중간·최대) + 하단 축선
function Grid({ max, y }) {
  return (
    <>
      {[0, max / 2, max].map((t) => (
        <g key={t}>
          <line x1={L} y1={y(t)} x2={R} y2={y(t)} stroke="var(--color-cell-line)" />
          <text x={L - 6} y={y(t) + 3.5} fontSize="10" fill="var(--color-g2)" textAnchor="end">
            {t}
          </text>
        </g>
      ))}
      <line x1={L} y1={B} x2={R} y2={B} stroke="var(--color-line)" />
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
  // 선 아래 은은한 area 한 겹 — 대시보드 플랫 톤을 넘지 않는 7% 단색
  const area = `${L},${B} ${pts} ${px(values.length - 1).toFixed(1)},${B}`
  const last = values.length - 1
  // x 라벨은 겹치지 않게 최대 8개만 고른다
  const step = Math.max(1, Math.ceil(ordered.length / 8))
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="block h-[300px] w-full font-mono">
      <Grid max={max} y={py} />
      <polygon points={area} fill={BLUE_HEX} opacity="0.07" />
      <polyline points={pts} fill="none" stroke={BLUE_HEX} strokeWidth="2" strokeLinejoin="round" />
      {values.map((v, i) => (
        <circle
          key={i}
          cx={px(i)}
          cy={py(v)}
          r={i === last ? 4.5 : 3.5}
          fill="#fff"
          stroke={BLUE_HEX}
          strokeWidth="2"
        />
      ))}
      {/* 마지막 값 라벨 — 대시보드 값 라벨 문법(700·g1) */}
      {values.length > 0 && (
        <text
          x={px(last)}
          y={py(values[last]) - 9}
          fontSize="10.5"
          fontWeight="700"
          fill="var(--color-g1)"
          textAnchor="middle"
        >
          {values[last]}
        </text>
      )}
      {ordered.map((r, i) =>
        i % step === 0 || i === last ? (
          <text key={i} x={px(i)} y={VH - 10} fontSize="10" fill="var(--color-g2)" textAnchor="middle">
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
// 최대 bin 은 진한 blue, 나머지는 연하게 — 피크가 한눈에 들어온다.
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
    bars = counts.map((c, i) => ({ label: `${(min + i * width).toFixed(1)}`, value: c }))
  }

  const max = niceMax(Math.max(1, ...bars.map((b) => b.value)))
  const peak = Math.max(...bars.map((b) => b.value))
  const py = (v) => B - (v / max) * (B - T)
  const gap = 6
  const bw = (R - L - gap * (bars.length - 1)) / bars.length
  const step = Math.max(1, Math.ceil(bars.length / 8))
  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="block h-[300px] w-full font-mono">
      <Grid max={max} y={py} />
      {bars.map((b, i) => {
        const h = (b.value / max) * (B - T)
        const bx = L + i * (bw + gap)
        const isPeak = b.value === peak && b.value > 0
        return (
          <g key={i}>
            {b.value > 0 && (
              <rect
                x={bx}
                y={B - h}
                width={bw}
                height={h}
                rx="2"
                fill={BLUE_HEX}
                opacity={isPeak ? 1 : 0.45}
              />
            )}
            {/* 값 라벨 — 피크는 항상, 그 외엔 막대가 적을 때만 (혼잡 방지) */}
            {b.value > 0 && (isPeak || bars.length <= 8) && (
              <text
                x={bx + bw / 2}
                y={B - h - 5}
                fontSize="10"
                fontWeight="700"
                fill="var(--color-g1)"
                textAnchor="middle"
              >
                {b.value}
              </text>
            )}
            {i % step === 0 || i === bars.length - 1 ? (
              <text x={bx + bw / 2} y={VH - 10} fontSize="10" fill="var(--color-g2)" textAnchor="middle">
                {b.label}
              </text>
            ) : null}
          </g>
        )
      })}
    </svg>
  )
}
