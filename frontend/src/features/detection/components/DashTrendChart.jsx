import { Card } from '../../../shared/components/ui/Card.jsx'

// 알람 추이 라인차트 — 시안 SVG(viewBox 860×300) 구조를 좌표 계산으로 재현한다.
// daily: [{ label: '6/1', oos, ooc, r03 }] — 일별 judgement 집계 (좌표 하드코딩 금지)
const X0 = 120 // 첫 데이터 포인트 x
const X1 = 780 // 마지막 데이터 포인트 x
const Y0 = 250 // y축 0 위치
const Y1 = 20 // y축 최대 위치
const Y_MAX = 30 // 눈금 0 / 10 / 20 / 30
const TICKS = [0, 10, 20, 30]

function DashTrendChart({ daily }) {
  const x = (i) => (daily.length > 1 ? X0 + (i * (X1 - X0)) / (daily.length - 1) : (X0 + X1) / 2)
  const y = (v) => Y0 - (Math.min(v, Y_MAX) / Y_MAX) * (Y0 - Y1)
  const pt = (i, v) => `${x(i)},${+y(v).toFixed(1)}`
  const oosPts = daily.map((d, i) => pt(i, d.oos)).join(' ')
  const oocPts = daily.map((d, i) => pt(i, d.ooc)).join(' ')

  return (
    <Card className="min-w-0 flex-1 px-5 pb-3 pt-4">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[15px] font-extrabold text-navy">알람 추이</span>
        <span className="flex items-center gap-4">
          <span className="flex items-center gap-1.5 text-[11.5px] font-bold text-red">
            <span className="h-2 w-2 rounded-full bg-red" />
            OOS
          </span>
          <span className="flex items-center gap-1.5 text-[11.5px] font-bold text-green">
            <span className="h-2 w-2 rounded-full bg-green" />
            OOC
          </span>
          <span className="text-[11.5px] text-g1">일 단위 · 기간을 좁히면 시간 단위</span>
        </span>
      </div>
      <svg viewBox="0 0 860 300" className="block w-full font-mono">
        {TICKS.map((t) => (
          <g key={t}>
            <line x1="60" y1={+y(t).toFixed(1)} x2="820" y2={+y(t).toFixed(1)} stroke="var(--color-line)" strokeWidth="1" />
            <text x="52" y={+(y(t) + 4).toFixed(1)} fontSize="10" fill="var(--color-g1)" textAnchor="end">
              {t}
            </text>
          </g>
        ))}
        {/* R03_CONSEC 발생일 — 세로 점선 + 상단 R03 칩 자동 배치 */}
        {daily.map(
          (d, i) =>
            d.r03 && (
              <g key={`r03-${d.label}`}>
                <line x1={x(i)} y1="34" x2={x(i)} y2={Y0} stroke="var(--color-g2)" strokeWidth="1" strokeDasharray="4 4" />
                <rect x={x(i) - 22} y="10" rx="9" width="44" height="18" fill="var(--color-red)" />
                <text x={x(i)} y="22.5" fontSize="9.5" fontWeight="700" fill="#fff" textAnchor="middle">
                  R03
                </text>
              </g>
            ),
        )}
        <polyline points={oocPts} fill="none" stroke="var(--color-green)" strokeWidth="2" />
        <polyline points={oosPts} fill="none" stroke="var(--color-red)" strokeWidth="2" />
        {daily.map((d, i) => (
          <circle key={`ooc-${d.label}`} cx={x(i)} cy={+y(d.ooc).toFixed(1)} r="4.5" fill="#fff" stroke="var(--color-green)" strokeWidth="2" />
        ))}
        {daily.map((d, i) => (
          <circle key={`oos-${d.label}`} cx={x(i)} cy={+y(d.oos).toFixed(1)} r="4.5" fill="#fff" stroke="var(--color-red)" strokeWidth="2" />
        ))}
        {daily.map((d, i) => (
          <text key={`x-${d.label}`} x={x(i)} y="272" fontSize="10" fill="var(--color-g1)" textAnchor="middle">
            {d.label}
          </text>
        ))}
      </svg>
      <div className="mt-1 rounded-lg border border-line bg-soft px-3.5 py-2.5 text-[11.5px] text-g1">
        점선은 <span className="font-mono font-bold text-navy">R03_CONSEC</span> 발생 시점 — 장비 정지 판정이 걸린 날
      </div>
    </Card>
  )
}

export default DashTrendChart
