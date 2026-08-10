import Badge from '../../../shared/components/ui/Badge.jsx'
import { Card, CardHeader, DashedCard } from '../../../shared/components/ui/Card.jsx'
import KVGrid from '../../../shared/components/ui/KVGrid.jsx'
import { ruleVariant } from '../../../shared/components/ui/statusStyles.js'
import { alarmSummary, detailNumbers } from './TraceModel.jsx'

// 우측 330px 컬럼 — 구간 통계 · anomaly_score · 이 구간의 알람 (시안 v2 03_트레이스뷰어)

const JUD_VARIANT = { OOS: 'bg-red', OOC: 'bg-amber', OK: 'bg-green' }
const num = (v) => (v == null ? '—' : v.toFixed(3))
const isR03 = (a) => String(a.rule_id).startsWith('R03')
const ruleShort = (r) => String(r).split('_')[0]
// occurred_at ISO(+09:00) → "HH:MM:SS"
const hms = (iso) => (String(iso ?? '').split('T')[1] ?? '').slice(0, 8)

const SOURCE_NOTE = {
  recomputed: '선택 웨이퍼 trace 포인트 재집계 (std = 표본 표준편차, ddof=1)',
  measured: '응답 measured_step_stats 제공값 (포인트 실측 미제공 조합)',
  none: '포인트 실측 미제공 + 제공 통계 없음',
}

// R03(연속) 알람 detail 에는 수치가 없다 — 같은 웨이퍼·스텝·파라미터의 수치 보유 알람(R01)
// detail 실측값으로 요약한다. 없으면 R03 자체 요약(수치 실측 미제공)으로 폴백.
const siblingOf = (a, all) =>
  all.find(
    (s) =>
      s.alarm_id !== a.alarm_id &&
      s.wafer_no === a.wafer_no &&
      s.recipe_step_name === a.recipe_step_name &&
      s.sensor_id === a.sensor_id &&
      detailNumbers(s.detail).max != null,
  )

function StepBox({ stat }) {
  return (
    <div className="rounded-lg border border-line bg-soft p-3.5">
      <div className="mb-3 flex items-center justify-between">
        <span className="font-mono text-[12.5px] font-bold text-navy">{stat.step}</span>
        {stat.judgement ? (
          <Badge variant={JUD_VARIANT[stat.judgement] ?? 't-gray'}>{stat.judgement}</Badge>
        ) : (
          <Badge variant="t-gray">실측 미제공</Badge>
        )}
      </div>
      {stat.source === 'none' ? (
        <div className="rounded-md border border-dashed border-dash-line bg-white px-2.5 py-2 text-[11.5px] font-semibold text-g1">
          {/* TODO(data): 포인트별 실측이 들어오면 재집계로 대체 */}
          mean/std/min/max 실측 미제공
          {stat.missingWafers?.length > 0 && (
            <div className="mt-1 font-mono text-[10.5px] font-normal text-g2">
              해당 스텝 실측 미제공: {stat.missingWafers.map((w) => `W${w}`).join(' · ')}
            </div>
          )}
        </div>
      ) : (
        <KVGrid
          items={[
            ['mean', num(stat.mean)],
            ['std', num(stat.std)],
            ['min', num(stat.min)],
            ['max', num(stat.max)],
          ]}
        />
      )}
      <div className="mt-2.5 text-[10px] text-g2">
        {SOURCE_NOTE[stat.source]}
        {stat.n != null && ` · ${stat.wafers}웨이퍼 ${stat.n}포인트`}
      </div>
    </div>
  )
}

// limitOf(sensor_id) — 알람 요약 문구는 그 알람 파라미터의 한계선·단위로 만든다 (전역 상수 금지)
function TraceSidePanel({ statsBySensor, anomaly, alarms, limitOf, focus }) {
  const groups = statsBySensor.filter((g) => g.stats.length > 0)
  const r03s = alarms.filter(isR03)
  const rest = alarms.filter((a) => !isR03(a)).slice().reverse() // 최신 알람이 위

  const scorePct = Math.min(100, Math.max(0, anomaly.score * 100))
  const thPct = Math.min(100, Math.max(0, anomaly.threshold * 100))

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader title="구간 통계" note="선택 WAFER 전체" />
        <div className="flex flex-col gap-3 px-4 pb-4">
          {groups.length === 0 && (
            <DashedCard className="px-3 py-4 text-center text-xs text-g1">스텝 통계 없음 — trace 실측 미제공</DashedCard>
          )}
          {groups.map((g) => (
            <div key={g.sensor} className="flex flex-col gap-3">
              {groups.length > 1 && <div className="font-mono text-[11.5px] font-bold text-blue">{g.sensor}</div>}
              {g.stats.map((s) => (
                <StepBox key={`${g.sensor}-${s.step}`} stat={s} />
              ))}
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <CardHeader title="anomaly_score" note="참고 값" mono />
        <div className="px-5 pb-[18px]">
          <div className="flex items-baseline gap-3.5">
            <span className="font-mono text-[30px] font-extrabold text-blue">{anomaly.score.toFixed(2)}</span>
            <span className="font-mono text-xs text-g1">임계 {anomaly.threshold.toFixed(2)}</span>
          </div>
          <div className="relative mt-3 h-2.5 rounded-full bg-cell-line">
            <div className="absolute left-0 top-0 h-full rounded-full bg-blue" style={{ width: `${scorePct}%` }} />
            <div className="absolute top-[-3px] h-4 w-0.5 bg-navy" style={{ left: `${thPct}%` }} />
          </div>
          <div className="mt-3.5 text-xs font-bold text-ink">판정에는 쓰지 않는다.</div>
          <div className="mt-1 text-[11.5px] text-g1">표시 · LLM 근거 · 평가 전용</div>
        </div>
      </Card>

      <Card>
        <CardHeader title="이 구간의 알람" note={`${alarms.length}건`} />
        <div className="flex flex-col gap-3 px-4 pb-4">
          {alarms.length === 0 && (
            <DashedCard className="px-3 py-4 text-center text-xs text-g1">조회 범위에 걸린 알람이 없습니다.</DashedCard>
          )}
          {r03s.map((a) => (
            <div
              key={a.alarm_id}
              className={`rounded-lg border bg-row-red p-3.5 ${focus.has(a.wafer_no) ? 'border-blue' : 'border-tint-red-line'}`}
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="font-mono text-[13px] font-bold text-navy">{a.alarm_id}</span>
                <Badge variant={ruleVariant(a.rule_id)}>{ruleShort(a.rule_id)}</Badge>
              </div>
              <div className="font-mono text-[11px] text-g1">
                W{a.wafer_no} · {a.recipe_step_name} · {hms(a.occurred_at)}
              </div>
              <div className="mt-2 font-mono text-xs font-bold text-red">
                {alarmSummary(siblingOf(a, alarms) ?? a, limitOf(a.sensor_id))}
              </div>
            </div>
          ))}
          {rest.map((a) => (
            <div
              key={a.alarm_id}
              className={`flex items-center gap-2 rounded-lg border bg-soft px-3 py-[9px] ${
                focus.has(a.wafer_no) ? 'border-blue' : 'border-line'
              }`}
            >
              <span className="whitespace-nowrap font-mono text-[11.5px] font-bold text-navy">{a.alarm_id}</span>
              <Badge variant={ruleVariant(a.rule_id)}>{ruleShort(a.rule_id)}</Badge>
              <span className="ml-auto whitespace-nowrap font-mono text-[11px] text-g1">
                W{a.wafer_no} · {hms(a.occurred_at)}
              </span>
            </div>
          ))}
          <DashedCard className="p-3.5">
            <div className="text-[12.5px] font-bold leading-[1.55] text-navy">
              선택한 파라미터 수만큼
              <br />
              차트가 세로로 쌓인다.
            </div>
            <div className="mt-2 text-[11.5px] leading-[1.6] text-g1">
              WAFER 를 여러 장 고르면
              <br />
              가로로 이어 붙고 경계에 구분선.
            </div>
          </DashedCard>
        </div>
      </Card>
    </div>
  )
}

export default TraceSidePanel
