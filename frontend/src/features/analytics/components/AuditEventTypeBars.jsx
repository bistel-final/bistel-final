// 이벤트 유형별 집계 — 9종 전부 표시하고 0건도 dim 회색 바로 남긴다 (숨기지 않음). 디자인 v2 07.
// 집계 기준은 현재 화면 slice가 아니라 "같은 필터의 전체 집합"이다.
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import HBar from '../../../shared/components/ui/HBar.jsx'

function AuditEventTypeBars({ eventTypes, counts }) {
  const max = Math.max(1, ...eventTypes.map((t) => counts[t] ?? 0))
  return (
    <Card>
      <CardHeader title="이벤트 유형별" note="같은 필터의 전체 집계" />
      <div className="flex flex-col gap-[13px] px-5 pb-[18px]">
        {eventTypes.map((t) => {
          const n = counts[t] ?? 0
          const zero = n === 0
          return (
            <div key={t}>
              <div className="mb-[5px] flex justify-between">
                <span className={`font-mono text-[10.5px] font-semibold ${zero ? 'text-g2' : 'text-ink'}`}>{t}</span>
                <span className={`font-mono text-[11px] font-bold ${zero ? 'text-g2' : 'text-ink'}`}>{n}</span>
              </div>
              {/* 폭은 값/최대 비율 — 0건은 시안 규칙대로 최소 4% 폭의 dim 바 */}
              <HBar value={zero ? max * 0.04 : n} max={max} height={7} dim={zero} />
            </div>
          )
        })}
      </div>
    </Card>
  )
}

export default AuditEventTypeBars
