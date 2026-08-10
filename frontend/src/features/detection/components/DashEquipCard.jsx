import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import HBar from '../../../shared/components/ui/HBar.jsx'

// 설비별 카드 — 설비 바(navy) 아래 챔버 바(blue 55% 투명)를 펼친다.
// 바 너비는 전부 값/최대값(설비 최대 건수) 비율. 0건 챔버는 '이상 없음' 회색 텍스트.
// equips: [{ id, n, chambers: [{ id, n }] }] 건수 내림차순
function DashEquipCard({ equips, onSelectChamber }) {
  const max = Math.max(...equips.map((e) => e.n), 1)
  return (
    <Card className="w-[520px] flex-none">
      <CardHeader title="설비별" note="챔버까지 펼치기" />
      <div className="flex flex-col gap-[18px] px-5 pb-5">
        {equips.map((e) => (
          <div key={e.id}>
            <div className="mb-1.5 flex justify-between">
              <span className="font-mono text-[13px] font-extrabold text-navy">{e.id}</span>
              <span className="font-mono text-[13px] font-extrabold text-navy">{e.n}건</span>
            </div>
            <HBar value={e.n} max={max} height={14} color="var(--color-navy)" />
            <div className="mt-2.5 flex flex-col gap-2">
              {e.chambers.map((c) => (
                <div
                  key={c.id}
                  className="flex cursor-pointer items-center gap-3 pl-5"
                  onClick={() => onSelectChamber(e.id, c.id)}
                >
                  <span className="w-[88px] flex-none font-mono text-[11px] text-g1">{c.id}</span>
                  <div className="flex-1">
                    <HBar value={c.n} max={max} height={8} opacity={0.55} />
                  </div>
                  <span
                    className={`w-14 flex-none text-right font-mono text-[11px] font-semibold ${
                      c.n > 0 ? 'text-ink' : 'text-g2'
                    }`}
                  >
                    {c.n > 0 ? `${c.n}건` : '이상 없음'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

export default DashEquipCard
