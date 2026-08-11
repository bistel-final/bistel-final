import Badge from '../../../shared/components/ui/Badge.jsx'
import { severityClass } from '../../../shared/components/ui/statusStyles.js'

// 조치 코드 solid 배지 — EQP_HOLD 적 · LOT_HOLD 황 · MONITOR 녹 (상태→색 매핑 고정)
const CODE_VARIANT = { EQP_HOLD: 'bg-red', LOT_HOLD: 'bg-amber', MONITOR: 'bg-green' }

<<<<<<< Updated upstream
// 권고 조치 섹션 — t-amber 톤 박스. 값은 run 응답의 recommended_action · severity · action_reason
function RunActionCard({ run, consec, rules }) {
  const code = run?.recommended_action ?? null
  const basis = run?.action_reason
    ? `근거 ${run.action_reason}`
    : consec
      ? `근거 R03_CONSEC — 연속 ${consec.hit_cnt} WAFER OOS`
      : rules?.length
        ? `근거 ${rules.map((r) => `${r.rule_id} ×${r.count}`).join(' · ')}`
        : '근거 룰 실측 미제공'
=======
// 권고 조치 섹션 — t-amber 톤 박스. 근거 문구는 알람 실측(R03 hit_cnt 등)에서 유도한다
function RunActionCard({ action, reason }) {
  const basis = reason ?? action?.reason ?? '조치 근거가 제공되지 않았습니다.'
>>>>>>> Stashed changes

  return (
    <div>
      <div className="mb-2 text-xs font-bold text-g1">권고 조치</div>
      <div className="rounded-lg border border-tint-amber-line bg-tint-amber p-4">
        {code ? (
          <>
            <div className="flex items-center gap-3">
              <Badge variant={CODE_VARIANT[code] ?? 'bg-gray'}>{code}</Badge>
              <span className="text-[12.5px] font-bold text-ink">
                심각도 <span className={severityClass(run.severity)}>{run.severity}</span>
              </span>
            </div>
            <div className="mt-3 font-mono text-[11.5px] text-ink">{basis}</div>
          </>
        ) : (
          <div className="text-[12.5px] font-bold text-g1">조치 정보 실측 미제공</div>
        )}
      </div>
    </div>
  )
}

export default RunActionCard
