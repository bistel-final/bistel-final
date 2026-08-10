// 근거 카드 미니 차트 — 시안 04 규격: viewBox 520×90, 기준선 점선 + 폴리라인.
// pts(시안 좌표 문자열)를 그대로 받거나, values+limit(실측)에서 좌표를 계산한다.
// 색은 전부 fdc.css :root 토큰 값 (임의 색 금지)
const COLORS = {
  red: '#B03A4E',
  blue: '#2062A8',
  green: '#2E7D4F',
  navy: '#1E3A5C',
  amber: '#B97F14',
}

const X0 = 10
const X1 = 510
const Y_TOP = 14
const Y_BOT = 84

function RunTraceChart({ color = 'red', pts, values, limit }) {
  let points = pts
  let baseY = 70 // 시안 좌표 모드의 기준선 위치

  // 실측 모드 — 기준선(USL)을 같은 스케일 위에 놓아 이탈 폭이 실제 비율로 보이게 한다
  if (!points && Array.isArray(values) && values.length > 1) {
    const lims = typeof limit === 'number' ? [limit] : []
    const lo = Math.min(...values, ...lims)
    const hi = Math.max(...values, ...lims)
    const span = hi - lo || 1
    const y = (v) => Y_BOT - ((v - lo) / span) * (Y_BOT - Y_TOP)
    if (lims.length) baseY = Number(y(limit).toFixed(1))
    points = values
      .map((v, i) => `${(X0 + ((X1 - X0) * i) / (values.length - 1)).toFixed(1)},${y(v).toFixed(1)}`)
      .join(' ')
  }

  if (!points) return null

  return (
    <svg viewBox="0 0 520 90" className="block w-full">
      {/* 기준선 — fdc --g2 */}
      <line x1={X0} y1={baseY} x2={X1} y2={baseY} stroke="#98A5B3" strokeWidth="1" strokeDasharray="4 4" opacity=".8" />
      <polyline points={points} fill="none" stroke={COLORS[color] ?? COLORS.red} strokeWidth="2" />
    </svg>
  )
}

export default RunTraceChart
