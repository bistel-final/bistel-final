import { useState } from 'react'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import { PHASE_DOT, eventLabel, eventPhase, isFailure } from './auditModel.js'

// 유형별 집계 — 화면 7 좌측 270px.
// 진흙색 바 9개 대신, 기간 전체를 하나의 분절 바로 보이고(navy 명도 사다리 + 실패 red)
// 아래에 점·라벨·숫자 목록을 둔다. 0건 유형은 접어 둔다 — 없는 것이 목록을 차지하지 않게.

function AuditEventTypeBars({ eventTypes, counts, total }) {
  const [showEmpty, setShowEmpty] = useState(false)
  const active = eventTypes.filter((t) => (counts[t] ?? 0) > 0)
  const empty = eventTypes.filter((t) => (counts[t] ?? 0) === 0)
  const sum = active.reduce((s, t) => s + counts[t], 0) || 1
  const failed = active.filter(isFailure).reduce((s, t) => s + counts[t], 0)

  return (
    <Card className="w-[270px] flex-none">
      <CardHeader title="유형별 집계" note={`${total}건`} />
      <div className="px-4 pb-4">
        <div className="flex h-2.5 w-full gap-[2px] overflow-hidden rounded-full bg-cell-line" role="img" aria-label="유형별 비율">
          {active.map((t) => (
            <div
              key={t}
              title={`${eventLabel(t)} ${counts[t]}건`}
              style={{ width: `${(counts[t] / sum) * 100}%`, background: PHASE_DOT[eventPhase(t)] }}
            />
          ))}
        </div>
        <div className="mt-2 flex items-baseline justify-between text-[11px]">
          <span className="text-g2">정상 {sum - failed}건</span>
          <span className={failed > 0 ? 'font-semibold text-fail' : 'text-faint'}>
            실패 {failed}건{failed > 0 && sum > 0 ? ` (${Math.round((failed / sum) * 100)}%)` : ''}
          </span>
        </div>

        <ul className="mt-4 flex flex-col gap-2.5">
          {active.map((t) => (
            <li key={t} className="flex items-center gap-2.5">
              <span className="h-2 w-2 flex-none rounded-full" style={{ background: PHASE_DOT[eventPhase(t)] }} />
              <span className="flex-1 truncate text-[12.5px] font-medium text-g1" title={t}>
                {eventLabel(t)}
              </span>
              <span className={`font-mono text-[12px] font-bold ${isFailure(t) ? 'text-fail' : 'text-navy'}`}>{counts[t]}</span>
            </li>
          ))}
          {active.length === 0 && <li className="text-[12px] text-faint">기간 내 기록이 없습니다</li>}
        </ul>

        {empty.length > 0 && (
          <div className="mt-4 border-t border-cell-line pt-3">
            <button
              type="button"
              onClick={() => setShowEmpty((v) => !v)}
              className="cursor-pointer text-[11.5px] text-g2 hover:text-navy"
            >
              기록 없는 유형 {empty.length}개 {showEmpty ? '접기' : '보기'}
            </button>
            {showEmpty && (
              <ul className="mt-2.5 flex flex-col gap-2">
                {empty.map((t) => (
                  <li key={t} className="flex items-center gap-2.5">
                    <span className="h-2 w-2 flex-none rounded-full border border-tint-gray-line bg-white" />
                    <span className="flex-1 truncate text-[12px] text-faint" title={t}>
                      {eventLabel(t)}
                    </span>
                    <span className="font-mono text-[12px] text-faint">0</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Card>
  )
}

export default AuditEventTypeBars
