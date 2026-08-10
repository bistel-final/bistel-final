// fdc.css .hbar-track / .hbar-fill(.dim) — 너비는 항상 값/최대값 비율로 계산 (하드코딩 % 금지)
function HBar({ value, max, height = 10, color, dim = false, opacity = 1 }) {
  const pct = max > 0 ? (value / max) * 100 : 0
  return (
    <div className="overflow-hidden rounded-full bg-cell-line" style={{ height }}>
      <div
        className={`h-full rounded-full ${color ? '' : dim ? 'bg-tint-gray-line' : 'bg-blue'}`}
        style={{ width: `${pct}%`, background: color, opacity }}
      />
    </div>
  )
}

export default HBar
