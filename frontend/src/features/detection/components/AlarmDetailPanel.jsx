import { Link } from 'react-router-dom'
import StatusBadge from '../../../shared/components/StatusBadge.jsx'
import { fmtDateTime, fmtTime } from '../../../shared/api/format.js'
import AlarmMiniTrace from './AlarmMiniTrace.jsx'

// 파라미터 → fault code 매핑. agent feature의 FAULT_BY_SENSOR와 값을 동일하게 맞춘 로컬 상수
// (feature 간 import 금지 규칙 때문에 복제한다 — 원본이 바뀌면 함께 갱신할 것)
const FAULT_BY_PARAM = {
  PH_FOCUS: { code: 'FOC', name: '포커스 이탈', cause: 'PH_FOCUS 연속 OOS — 포커스 이탈로 CD_ADI 불량 유발' },
  ET_REFL: { code: 'RFM', name: 'RF 정합 이상', cause: '반사파 상승 = RF 정합 불량, 실효 전력 저하' },
  ET_CF4: { code: 'MFD', name: 'CF4 유량 저하', cause: 'CF4 유량 저하로 식각 부족' },
}

const APPROVAL_TONE = { PENDING: 'ooc', APPROVED: 'ok', AUTO: 'blue' }
const APPROVAL_LABEL = { PENDING: '승인 대기', APPROVED: '승인 완료', AUTO: '자동 승인' }

const ruleTone = (r) => (r === 'R03_CONSEC' ? 'oos' : 'neutral')
const judTone = (j) => (j === 'OOS' ? 'oos' : 'ooc')
const dash = (v) => (v === null || v === undefined || v === '' ? '—' : v)

// 같은 incident = lot_id + chamber_id + sensor_id
const incidentKey = (a) => `${a.incident?.lot_id ?? a.lot_id}|${a.incident?.chamber_id ?? a.chamber_id}|${a.incident?.sensor_id ?? a.sensor_id}`

function Field({ label, children }) {
  return (
    <div className="rounded-lg bg-page px-3 py-[7px]">
      <div className="text-[11px] font-bold text-slate-light">{label}</div>
      <div className="mt-0.5 font-mono text-[13px] font-extrabold text-navy">{children}</div>
    </div>
  )
}

function SectionTitle({ children, right }) {
  return (
    <div className="mb-2 flex items-center gap-2">
      <div className="text-[13.5px] font-extrabold text-navy">{children}</div>
      {right && <div className="ml-auto">{right}</div>}
    </div>
  )
}

function AlarmDetailPanel({ alarm, alarms, catalog, area, onClose, onSelect }) {
  const siblings = alarms
    .filter((a) => incidentKey(a) === incidentKey(alarm))
    .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at) || a.alarm_id.localeCompare(b.alarm_id))
  const pos = siblings.findIndex((a) => a.alarm_id === alarm.alarm_id)
  const prev = pos > 0 ? siblings[pos - 1] : null
  const next = pos >= 0 && pos < siblings.length - 1 ? siblings[pos + 1] : null

  const waferTrace = (catalog?.wafers ?? []).find((w) => w.lot_hist_id === alarm.lot_hist_id) ?? null

  const traceQuery = new URLSearchParams({
    area,
    equipment: alarm.equipment_id ?? '',
    chamber: alarm.chamber_id ?? '',
    sensor: alarm.sensor_id ?? '',
    lot: alarm.lot_id ?? '',
    wafer: alarm.wafer_no ?? '',
  }).toString()

  const fault = FAULT_BY_PARAM[alarm.sensor_id] ?? null

  return (
    <aside className="sticky top-4 flex animate-[om-fadein_.2s_ease-out] flex-col gap-4 rounded-xl border border-line bg-white p-4 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div>
        <div className="flex items-start gap-2">
          <div className="font-mono text-[17px] font-extrabold text-navy">{alarm.alarm_id}</div>
          <StatusBadge tone={ruleTone(alarm.rule_id)} mono>
            {alarm.rule_id}
          </StatusBadge>
          <StatusBadge tone={judTone(alarm.judgement)} mono>
            {alarm.judgement}
          </StatusBadge>
          <button
            type="button"
            onClick={onClose}
            aria-label="상세 닫기"
            className="ml-auto flex h-7 w-7 cursor-pointer items-center justify-center rounded-lg border border-line-input bg-white text-base font-bold text-slate hover:bg-line-soft"
          >
            ×
          </button>
        </div>
        <div className="mt-1 font-mono text-[12.5px] font-semibold text-slate">{fmtDateTime(alarm.occurred_at)}</div>
        <div className="mt-2.5 grid grid-cols-3 gap-2">
          <Field label="LOT">{dash(alarm.lot_id)}</Field>
          <Field label="WAFER">{alarm.wafer_no ? `W${alarm.wafer_no}` : '—'}</Field>
          <Field label="챔버">{dash(alarm.chamber_id)}</Field>
          <Field label="스텝">{dash(alarm.recipe_step_name)}</Field>
          <Field label="파라미터">{dash(alarm.sensor_id)}</Field>
          <Field label="hit_cnt">{dash(alarm.hit_cnt)}</Field>
        </div>
        {alarm.detail && (
          <div className="mt-2 rounded-lg border border-line bg-page px-3 py-2 font-mono text-[12.5px] font-bold text-ink">
            {alarm.detail}
          </div>
        )}
      </div>

      <div className="border-t border-line-soft pt-3.5">
        <SectionTitle
          right={
            <Link to={`/traces?${traceQuery}`} className="text-[12.5px] font-bold text-brand hover:text-brand-light">
              크게 보기 →
            </Link>
          }
        >
          미니 파형
        </SectionTitle>
        <AlarmMiniTrace wafer={waferTrace} limits={catalog?.limits} />
      </div>

      <div className="border-t border-line-soft pt-3.5">
        <SectionTitle
          right={
            <span className="text-[12px] font-bold text-slate">
              {siblings.length}건 중 <span className="font-mono font-extrabold text-navy">{pos + 1}</span>번째
            </span>
          }
        >
          같은 incident
        </SectionTitle>
        <div className="mb-2 font-mono text-[11.5px] font-semibold text-slate-light">
          {alarm.lot_id} · {alarm.chamber_id} · {alarm.sensor_id}
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            disabled={!prev}
            onClick={() => prev && onSelect(prev.alarm_id)}
            aria-label="이전 알람"
            className="flex h-7 w-7 flex-none items-center justify-center rounded-lg border border-line-input bg-white text-[13px] font-extrabold text-slate enabled:cursor-pointer enabled:hover:bg-line-soft disabled:opacity-35"
          >
            ‹
          </button>
          <div className="flex flex-1 flex-wrap gap-1.5">
            {siblings.map((s) => {
              const on = s.alarm_id === alarm.alarm_id
              return (
                <button
                  key={s.alarm_id}
                  type="button"
                  onClick={() => onSelect(s.alarm_id)}
                  title={`${s.alarm_id} · ${fmtDateTime(s.occurred_at)} · ${s.rule_id}`}
                  className="cursor-pointer rounded-full border px-2.5 py-[3px] font-mono text-[11px] font-extrabold"
                  style={
                    on
                      ? { background: '#1E5FC2', color: '#FFFFFF', borderColor: '#1E5FC2' }
                      : {
                          background: '#FFFFFF',
                          color: s.rule_id === 'R03_CONSEC' ? '#DC2626' : '#475569',
                          borderColor: s.rule_id === 'R03_CONSEC' ? '#FCA5A5' : '#CBD5E1',
                        }
                  }
                >
                  W{s.wafer_no} {fmtTime(s.occurred_at)}
                </button>
              )
            })}
          </div>
          <button
            type="button"
            disabled={!next}
            onClick={() => next && onSelect(next.alarm_id)}
            aria-label="다음 알람"
            className="flex h-7 w-7 flex-none items-center justify-center rounded-lg border border-line-input bg-white text-[13px] font-extrabold text-slate enabled:cursor-pointer enabled:hover:bg-line-soft disabled:opacity-35"
          >
            ›
          </button>
        </div>
      </div>

      <div className="border-t border-line-soft pt-3.5">
        <SectionTitle
          right={
            alarm.latest_agent_run_id ? (
              <Link
                to={`/agent-runs/${alarm.latest_agent_run_id}`}
                className="text-[12.5px] font-bold text-brand hover:text-brand-light"
              >
                분석 보기 →
              </Link>
            ) : null
          }
        >
          Agent 판단
        </SectionTitle>
        <div className="flex flex-col gap-2.5 rounded-[10px] border border-[#BFDBFE] bg-[#F0F6FF] px-3.5 py-3">
          {fault ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-brand px-2.5 py-1 font-mono text-[11.5px] font-extrabold text-white">
                  FAULT {fault.code}
                </span>
                <span className="text-[13px] font-extrabold text-navy">{fault.name}</span>
              </div>
              <div className="text-[12.5px] font-medium leading-[1.55] text-ink">{fault.cause}</div>
            </>
          ) : (
            // TODO(data): 이 파라미터의 fault code·원인 실측 미제공
            <div className="text-[12.5px] font-semibold text-slate">fault code 실측 미제공</div>
          )}
          <div className="flex flex-wrap items-center gap-2 border-t border-[#DBEAFE] pt-2">
            <span className="text-[12px] font-bold text-slate">조치</span>
            {alarm.action_id ? (
              <Link
                to={`/actions?action=${alarm.action_id}`}
                className="font-mono text-[12.5px] font-extrabold text-brand hover:text-brand-light"
              >
                {alarm.action_id}
              </Link>
            ) : (
              <span className="font-mono text-[12.5px] font-extrabold text-slate-light">—</span>
            )}
            {alarm.action_code ? (
              <StatusBadge tone="navy" mono>
                {alarm.action_code}
              </StatusBadge>
            ) : (
              <span className="font-mono text-[12.5px] font-extrabold text-slate-light">—</span>
            )}
            {alarm.approval_status ? (
              <StatusBadge tone={APPROVAL_TONE[alarm.approval_status] ?? 'neutral'}>
                {APPROVAL_LABEL[alarm.approval_status] ?? alarm.approval_status}
              </StatusBadge>
            ) : (
              // TODO(data): 승인 상태 실측 미제공
              <span className="text-[12px] font-bold text-slate-light">승인 상태 —</span>
            )}
          </div>
        </div>
      </div>
    </aside>
  )
}

export default AlarmDetailPanel
