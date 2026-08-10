import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'
import { actionCodeVariant } from '../../../shared/components/ui/statusStyles.js'
import { toIso } from '../../../shared/api/format.js'

// 데모용 기준 시각 — fixture가 2026-06-04 07:49 시점의 스냅샷이라는 전제로 상대시각을 계산한다.
// 실서비스 전환 시 서버 현재 시각으로 대체한다.
const DEMO_NOW = '2026-06-04 07:49'

// 60분 미만 "N분 전", 이상 "N시간 전"
const agoOf = (iso) => {
  const min = Math.floor((new Date(toIso(DEMO_NOW)) - new Date(iso)) / 60000)
  return min < 60 ? `${min}분 전` : `${Math.floor(min / 60)}시간 전`
}

// 실행 실패 / 전송 실패 회색 카드 — 건수는 데이터 집계값 (0이 정상 상태)
function FailCard({ title, count, emptyNote, note }) {
  return (
    <div className="flex w-[170px] flex-none flex-col items-center justify-center gap-1.5 rounded-lg border border-line bg-soft p-4">
      <span className="text-[12.5px] font-bold text-g1">{title}</span>
      <span className={`font-mono text-[28px] font-extrabold ${count > 0 ? 'text-red' : 'text-g2'}`}>{count}</span>
      <span className="text-[11px] text-g2">{count > 0 ? note : emptyNote}</span>
    </div>
  )
}

// 「처리 필요」 밴드 — 좌측 3px 적색 보더, 승인 대기 리스트 + 실행/전송 실패 카드
function DashActionBand({ pendings, runFailed, sendFailed, onReview }) {
  return (
    <Card className="mt-2 border-l-[3px] border-l-red px-5 py-4">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-[15px] font-extrabold text-navy">처리 필요</span>
        <span className="text-[11.5px] text-g1">내가 판단해야 하는 것만</span>
      </div>
      <div className="flex items-stretch gap-4">
        <div className="flex-1 rounded-lg border border-tint-red-line bg-row-red px-4 py-3.5">
          <div className="mb-2.5 flex items-center gap-2.5">
            <span className="text-[13px] font-extrabold text-red">승인 대기</span>
            <Badge variant="bg-red">{pendings.length}</Badge>
          </div>
          <div className="flex flex-col gap-2">
            {pendings.length === 0 && (
              <div className="rounded-lg border border-line bg-white px-3.5 py-2.5 text-xs text-g2">
                대기 중인 승인 요청이 없습니다
              </div>
            )}
            {pendings.map((p) => (
              <div
                key={p.approval_id}
                className="flex items-center gap-4 rounded-lg border border-line bg-white px-3.5 py-2.5"
              >
                <span className="font-mono text-[12.5px] font-bold text-navy">{p.approval_id}</span>
                <span className="font-mono text-xs">
                  {p.incident.lot_id} · {p.incident.chamber_id} · {p.incident.sensor_id}
                </span>
                <Badge variant={actionCodeVariant(p.action_code)}>{p.action_code}</Badge>
                {p.rule && <span className="font-mono text-[11px] text-g1">{p.rule}</span>}
                <span className="ml-auto font-mono text-[11px] text-g1">{agoOf(p.requested_at)}</span>
                <Button sm onClick={() => onReview(p.run_id)}>
                  검토
                </Button>
              </div>
            ))}
          </div>
        </div>
        <FailCard title="실행 실패" count={runFailed} emptyNote="재실행 대상 없음" note="재실행 필요" />
        <FailCard title="전송 실패" count={sendFailed} emptyNote="재시도 대상 없음" note="재시도 필요" />
      </div>
    </Card>
  )
}

export default DashActionBand
