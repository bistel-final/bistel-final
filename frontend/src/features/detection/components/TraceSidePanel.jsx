import { fmtShort } from '../../../shared/api/format.js'
import StatusBadge from '../../../shared/components/StatusBadge.jsx'
import { alarmSummary } from './TraceModel.jsx'

// 우측 패널 3블록: 구간 통계 · anomaly_score 게이지 · 이 구간의 알람

const CARD = 'rounded-xl border border-line bg-white px-[18px] py-[15px] shadow-[0_1px_3px_rgba(15,42,92,.05)]'
const BLOCK_TITLE = 'text-sm font-extrabold text-navy'
const NA = <span className="font-mono text-[13px] font-bold text-slate-light">실측 미제공</span>

const judTone = (j) => (j === 'OOS' ? 'oos' : j === 'OOC' ? 'ooc' : j === 'OK' ? 'ok' : 'muted')
const num = (v) => (v == null ? null : v.toFixed(3))

const SOURCE_NOTE = {
  recomputed: '선택 웨이퍼 trace 포인트 재집계 (std = 표본 표준편차, ddof=1)',
  measured: 'MEASURED_STEP_STATS 실측 제공값 (포인트 복원 불가 조합)',
  none: '포인트 복원 불가 + 제공 통계 없음',
}

function StatCell({ label, value }) {
  return (
    <div className="rounded-md bg-page px-2.5 py-1.5">
      <div className="text-[10.5px] font-bold text-slate-light">{label}</div>
      <div className="font-mono text-[14px] font-extrabold text-navy">{value ?? '—'}</div>
    </div>
  )
}

function StepStatCard({ stat }) {
  return (
    <div className="rounded-[10px] border border-line px-3 py-2.5">
      <div className="mb-2 flex items-center gap-2">
        <span className="font-mono text-[12.5px] font-extrabold text-navy">{stat.step}</span>
        {stat.judgement ? (
          <StatusBadge tone={judTone(stat.judgement)} mono className="ml-auto">
            {stat.judgement}
          </StatusBadge>
        ) : (
          <span className="ml-auto">{NA}</span>
        )}
      </div>
      {stat.source === 'none' ? (
        <div className="rounded-md border border-dashed border-line-input bg-page px-2.5 py-2 text-[11.5px] font-semibold text-slate">
          {/* TODO(data): 포인트별 실측이 들어오면 재집계로 대체 */}
          mean/std/min/max 실측 미제공
          {stat.missingWafers?.length > 0 && (
            <div className="mt-1 font-mono text-[11px] text-slate-light">
              포인트 없음: {stat.missingWafers.map((w) => `W${w}`).join(' · ')}
            </div>
          )}
          {stat.noteMeans?.length > 0 && (
            <div className="mt-1 font-mono text-[11px] text-slate-light">
              알람 detail 상 스텝 mean: {stat.noteMeans.map((m) => m.toFixed(3)).join(' · ')}
            </div>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-1.5">
          <StatCell label="mean" value={num(stat.mean)} />
          <StatCell label="std" value={num(stat.std)} />
          <StatCell label="min" value={num(stat.min)} />
          <StatCell label="max" value={num(stat.max)} />
        </div>
      )}
      <div className="mt-1.5 text-[10.5px] font-semibold text-slate-light">
        {SOURCE_NOTE[stat.source]}
        {stat.n != null && ` · ${stat.wafers}웨이퍼 ${stat.n}포인트`}
      </div>
    </div>
  )
}

function TraceSidePanel({ statsBySensor, anomaly, alarms, lim, focus }) {
  const scorePct = Math.min(100, Math.max(0, anomaly.score * 100))
  const thPct = Math.min(100, Math.max(0, anomaly.threshold * 100))

  return (
    <div className="flex flex-col gap-3.5">
      <div className={CARD}>
        <div className={BLOCK_TITLE}>구간 통계</div>
        <div className="mt-1 text-[11px] font-semibold text-slate-light">
          선택된 웨이퍼들의 trace 포인트로 클라이언트에서 재집계한다.
        </div>
        <div className="mt-2.5 flex flex-col gap-2.5">
          {statsBySensor.map((g) => (
            <div key={g.sensor} className="flex flex-col gap-2">
              <div className="font-mono text-[12px] font-extrabold text-brand">{g.sensor}</div>
              {g.stats.length === 0 ? (
                <div className="rounded-[10px] border border-dashed border-line-input bg-page px-3 py-3 text-center text-[12.5px] font-semibold text-slate">
                  스텝 데이터 없음
                </div>
              ) : (
                g.stats.map((s) => <StepStatCard key={`${g.sensor}-${s.step}`} stat={s} />)
              )}
            </div>
          ))}
        </div>
      </div>

      <div className={CARD}>
        <div className="flex items-baseline gap-2">
          <span className={BLOCK_TITLE}>anomaly_score</span>
          <span className="ml-auto font-mono text-[26px] font-extrabold text-navy">{anomaly.score.toFixed(2)}</span>
        </div>
        <div className="relative mt-7 h-2.5 rounded-[5px] bg-line">
          <div
            className="absolute bottom-0 left-0 top-0 rounded-[5px] bg-[linear-gradient(90deg,#2E7BE8,#35B5C8)]"
            style={{ width: `${scorePct}%` }}
          />
          <div className="absolute -bottom-1 -top-2 w-[2.5px] bg-navy" style={{ left: `${thPct}%` }} />
          <div
            className="absolute -top-6 -translate-x-1/2 whitespace-nowrap font-mono text-[11px] font-extrabold text-navy"
            style={{ left: `${thPct}%` }}
          >
            임계 {anomaly.threshold.toFixed(2)}
          </div>
        </div>
        <div className="mt-3 rounded-lg border border-line-input bg-page px-3 py-2 text-[12px] font-bold text-slate">
          참고 지표 — <span className="text-navy">판정에는 쓰지 않는다.</span> OOS/OOC 판정은 한계선(USL {lim.USL} /
          UCL {lim.UCL}) 과 룰 엔진 결과만 사용한다.
        </div>
      </div>

      <div className={CARD}>
        <div className="flex items-baseline gap-2">
          <span className={BLOCK_TITLE}>이 구간의 알람</span>
          <span className="ml-auto font-mono text-[12.5px] font-extrabold text-slate">{alarms.length}건</span>
        </div>
        <div className="mt-2.5 flex flex-col gap-2">
          {alarms.length === 0 && (
            <div className="rounded-[10px] border border-dashed border-line-input bg-page px-3 py-4 text-center text-[12.5px] font-semibold text-slate">
              조회 범위에 걸린 알람이 없습니다.
            </div>
          )}
          {alarms.map((a) => (
            <div
              key={a.alarm_id}
              className="rounded-[10px] border px-3 py-2.5"
              style={{
                borderColor: focus.has(a.wafer_no) ? '#1E5FC2' : '#E2E8F0',
                background: focus.has(a.wafer_no) ? '#F7FAFF' : '#FFFFFF',
              }}
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[12.5px] font-extrabold text-navy">{a.alarm_id}</span>
                <StatusBadge tone={a.judgement === 'OOS' ? 'oos' : 'ooc'} mono>
                  {a.judgement}
                </StatusBadge>
                <StatusBadge tone={a.rule_id === 'R03_CONSEC' ? 'critical' : 'neutral'} mono>
                  {a.rule_id}
                </StatusBadge>
                <span className="ml-auto font-mono text-[11px] font-bold text-slate-light">
                  {fmtShort(a.occurred_at)}
                </span>
              </div>
              <div className="mt-1.5 font-mono text-[11.5px] font-bold text-slate">
                W{a.wafer_no} · {a.recipe_step_name} · {a.sensor_id}
              </div>
              <div className="mt-1 font-mono text-[12.5px] font-extrabold text-ink">{alarmSummary(a, lim)}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default TraceSidePanel
