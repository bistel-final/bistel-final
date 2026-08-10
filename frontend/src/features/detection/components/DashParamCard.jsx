import { Card, CardHeader, DashedCard } from '../../../shared/components/ui/Card.jsx'
import HBar from '../../../shared/components/ui/HBar.jsx'

// 파라미터별 카드 — 알람 집계에서 유도한 건수를 HBar(값/최대값 비율)로 표시.
// params: [{ name, n, chambers }] 건수 내림차순 / quiet: 기간 내 이상 없는 파라미터 목록
function DashParamCard({ params, quiet, totalKinds, onSelect }) {
  const max = Math.max(...params.map((p) => p.n), 1)
  return (
    <Card className="w-[400px] flex-none">
      <CardHeader title="파라미터별" note="막대를 누르면 필터" />
      <div className="flex flex-col gap-3.5 px-5 pb-[18px]">
        {params.map((p) => (
          <div key={p.name} className="cursor-pointer" onClick={() => onSelect(p.name)}>
            <div className="mb-1.5 flex justify-between">
              <span className="font-mono text-xs font-bold text-navy">{p.name}</span>
              <span className="font-mono text-xs font-bold text-blue">{p.n}건</span>
            </div>
            <HBar value={p.n} max={max} />
            <div className="mt-[3px] font-mono text-[10.5px] text-g2">{p.chambers}</div>
          </div>
        ))}
        <DashedCard className="px-3.5 py-3">
          <div className="text-xs font-bold text-navy">알람이 발생한 파라미터 {params.length} 종</div>
          <div className="mt-1 text-[11px] text-g1">나머지 {totalKinds - params.length} 종은 기간 내 이상 없음</div>
          {quiet.length > 0 && <div className="mt-1.5 font-mono text-[10px] text-g2">{quiet.join(' · ')}</div>}
        </DashedCard>
      </div>
    </Card>
  )
}

export default DashParamCard
