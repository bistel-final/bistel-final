import { useCallback, useEffect, useMemo, useState } from 'react'
import { getAlarms } from '../../../shared/api/detection.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'

const CH_OPTS = ['전체', 'PHO-01-C1', 'PHO-01-C2', 'ETC-01-C1', 'ETC-01-C2']
const SENSOR_OPTS = ['전체', 'PH_DOSE', 'PH_FOCUS', 'PH_PEB', 'PH_DEV', 'ET_PRES', 'ET_REFL', 'ET_CF4', 'ET_ESC']
const SUM_CHIPS = [
  { label: '전체', val: '51', color: '#0F2A5C' },
  { label: 'OOS', val: '37', color: '#DC2626' },
  { label: 'OOC', val: '14', color: '#D97706' },
  { label: 'R01', val: '34', color: '#0F2A5C' },
  { label: 'R02', val: '14', color: '#0F2A5C' },
  { label: 'R03', val: '3', color: '#DC2626' },
]

// Agent 처리상태: 승인 대기 2건(ALM-0022·ALM-0048) / 6-4 발생분 미처리 / 나머지 완료
const statusOf = (a) =>
  a.alarm_id === 'ALM-0022' || a.alarm_id === 'ALM-0048'
    ? 'WAITING_APPROVAL'
    : String(a.occurred_at || '').startsWith('2026-06-04')
      ? '미처리'
      : 'COMPLETED'

const ruleColor = (r) =>
  r === 'R03_CONSEC' ? ['#DC2626', '#FFFFFF'] : r === 'R02_OOC' ? ['#FEF3C7', '#D97706'] : ['#FEE2E2', '#DC2626']
const judColor = (j) => (j === 'OOS' ? ['#FEE2E2', '#DC2626'] : ['#FEF3C7', '#D97706'])
const stColor = (s) =>
  s === 'COMPLETED' ? ['#DCFCE7', '#16A34A'] : s === 'WAITING_APPROVAL' ? ['#FEF3C7', '#D97706'] : ['#F1F5F9', '#475569']

const numFrom = (detail, re) => {
  const m = String(detail || '').match(re)
  return m ? m[1] : '—'
}

function ToggleGroup({ options, value, onChange, mono = false }) {
  return (
    <div className="flex overflow-hidden rounded-lg border border-line-input bg-white">
      {options.map((l) => (
        <div
          key={l}
          onClick={() => onChange(l)}
          className={`cursor-pointer px-3.5 py-[7px] text-[13.5px] font-bold ${mono ? 'font-mono text-[13px] px-3' : ''}`}
          style={value === l ? { background: '#1E5FC2', color: '#FFFFFF' } : { background: '#FFFFFF', color: '#475569' }}
        >
          {l}
        </div>
      ))}
    </div>
  )
}

function AlarmsPage() {
  const [alarms, setAlarms] = useState(null)
  const [error, setError] = useState(null)
  const [fJud, setFJud] = useState('전체')
  const [fRule, setFRule] = useState('전체')
  const [fCh, setFCh] = useState('전체')
  const [fSensor, setFSensor] = useState('전체')
  const [fLot, setFLot] = useState('')
  const [selId, setSelId] = useState(null)
  const [acc, setAcc] = useState({ 1: true, 2: false, 3: false })

  const load = useCallback(() => {
    getAlarms()
      .then(setAlarms)
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const filtered = useMemo(() => {
    if (!alarms) return []
    return alarms.filter(
      (a) =>
        (fJud === '전체' || a.judgement === fJud) &&
        (fRule === '전체' || a.rule_id === fRule) &&
        (fCh === '전체' || a.chamber_id === fCh) &&
        (fSensor === '전체' || a.sensor_id === fSensor) &&
        (!fLot || String(a.lot_id || '').toLowerCase().includes(fLot.toLowerCase())),
    )
  }, [alarms, fJud, fRule, fCh, fSensor, fLot])

  if (error) return <ErrorState detail={error} onRetry={() => { setError(null); load() }} />
  if (!alarms) return <LoadingState message="알람 목록을 불러오는 중…" />

  const sel = alarms.find((a) => a.alarm_id === selId)
  const pJud = sel ? judColor(sel.judgement) : ['#F1F5F9', '#475569']
  const isConsec = sel && sel.rule_id === 'R03_CONSEC'
  const toggleAcc = (n) => setAcc((s) => ({ ...s, [n]: !s[n] }))

  return (
    <>
      <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
        <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">알람 목록</div>
        <div className="flex flex-wrap items-center gap-[18px] rounded-xl border border-line bg-white px-[18px] py-3.5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-bold text-slate">판정</span>
            <ToggleGroup options={['전체', 'OOS', 'OOC']} value={fJud} onChange={setFJud} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-bold text-slate">규칙</span>
            <ToggleGroup options={['전체', 'R01_OOS', 'R02_OOC', 'R03_CONSEC']} value={fRule} onChange={setFRule} mono />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-bold text-slate">챔버</span>
            <select
              value={fCh}
              onChange={(e) => setFCh(e.target.value)}
              className="rounded-lg border border-line-input bg-white px-2.5 py-[7px] font-mono text-[13.5px] font-semibold text-navy"
            >
              {CH_OPTS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-bold text-slate">센서</span>
            <select
              value={fSensor}
              onChange={(e) => setFSensor(e.target.value)}
              className="rounded-lg border border-line-input bg-white px-2.5 py-[7px] font-mono text-[13.5px] font-semibold text-navy"
            >
              {SENSOR_OPTS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </div>
          <input
            type="text"
            value={fLot}
            onChange={(e) => setFLot(e.target.value)}
            placeholder="LOT 검색 (예: LOT-260010)"
            className="w-[220px] rounded-lg border border-line-input px-3 py-2 font-mono text-[13.5px] font-semibold text-navy"
          />
        </div>
        <div className="flex flex-wrap gap-2.5">
          {SUM_CHIPS.map((c) => (
            <span
              key={c.label}
              className="inline-flex items-center gap-[7px] rounded-full border border-line bg-white px-3.5 py-1.5 text-[13px] font-bold text-slate"
            >
              {c.label}
              <span className="font-mono text-sm font-extrabold" style={{ color: c.color }}>
                {c.val}
              </span>
            </span>
          ))}
        </div>
        <div className="overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <div className="grid grid-cols-[96px_150px_110px_60px_104px_96px_122px_66px_1fr] gap-2 border-b border-line bg-page px-4 py-3 text-[12.5px] font-extrabold text-slate">
            <span>alarm_id</span>
            <span>발생시각</span>
            <span>LOT</span>
            <span>WAFER</span>
            <span>챔버</span>
            <span>센서</span>
            <span>규칙</span>
            <span>판정</span>
            <span>Agent 처리상태</span>
          </div>
          {filtered.length === 0 && (
            <div className="p-12 text-center text-[15px] font-semibold text-slate">
              조건에 맞는 알람이 없습니다. 필터를 조정해 주세요.
            </div>
          )}
          <div className="max-h-[560px] overflow-auto">
            {filtered.map((a) => {
              const st = statusOf(a)
              const rc = ruleColor(a.rule_id)
              const jc = judColor(a.judgement)
              const sc = stColor(st)
              return (
                <div
                  key={a.alarm_id}
                  onClick={() => setSelId(a.alarm_id)}
                  className="grid cursor-pointer grid-cols-[96px_150px_110px_60px_104px_96px_122px_66px_1fr] items-center gap-2 border-b border-line-soft px-4 py-2.5 transition-colors duration-[120ms] hover:bg-line-soft"
                  style={{
                    background:
                      selId === a.alarm_id ? '#DBEAFE' : a.rule_id === 'R03_CONSEC' ? '#FEF2F2' : 'transparent',
                  }}
                >
                  <span className="font-mono text-[13.5px] font-extrabold text-navy">{a.alarm_id}</span>
                  <span className="font-mono text-[13px] font-semibold text-ink">
                    {String(a.occurred_at || '').slice(5, 16)}
                  </span>
                  <span className="font-mono text-[13px] font-semibold text-ink">{a.lot_id}</span>
                  <span className="font-mono text-[13px] font-semibold text-ink">{a.wafer_no}</span>
                  <span className="font-mono text-[13px] font-semibold text-ink">{a.chamber_id}</span>
                  <span className="font-mono text-[13px] font-semibold text-ink">{a.sensor_id}</span>
                  <span>
                    <span
                      className="rounded-md px-2 py-[3px] font-mono text-[11.5px] font-extrabold"
                      style={{ background: rc[0], color: rc[1] }}
                    >
                      {a.rule_id}
                    </span>
                  </span>
                  <span>
                    <span
                      className="rounded-md px-2 py-[3px] font-mono text-[11.5px] font-extrabold"
                      style={{ background: jc[0], color: jc[1] }}
                    >
                      {a.judgement}
                    </span>
                  </span>
                  <span>
                    <span
                      className="rounded-md px-2 py-[3px] text-[11.5px] font-extrabold"
                      style={{ background: sc[0], color: sc[1] }}
                    >
                      {st}
                    </span>
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      </div>
      {sel && (
        <div
          onClick={() => setSelId(null)}
          className="fixed bottom-0 left-[236px] right-0 top-[60px] z-[19] animate-[om-fadein_.2s] bg-[rgba(15,42,92,.25)]"
        />
      )}
      <div
        className="fixed bottom-0 right-0 top-[60px] z-20 flex w-[560px] flex-col border-l border-line bg-white shadow-[-12px_0_32px_rgba(15,42,92,.15)] transition-transform duration-300 ease-[cubic-bezier(.2,.8,.3,1)]"
        style={{ transform: sel ? 'translateX(0)' : 'translateX(105%)' }}
      >
        <div className="flex items-center gap-3 border-b border-line bg-page px-[22px] py-[18px]">
          <div>
            <div className="font-mono text-[19px] font-extrabold text-navy">{sel ? sel.alarm_id : '—'}</div>
            <div className="mt-[3px] font-mono text-[13px] font-semibold text-slate">{sel ? sel.occurred_at : ''}</div>
          </div>
          <span
            className="rounded-md px-2.5 py-1 font-mono text-xs font-extrabold"
            style={{ background: pJud[0], color: pJud[1] }}
          >
            {sel ? sel.judgement : '—'}
          </span>
          <div
            onClick={() => setSelId(null)}
            className="ml-auto flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg border border-line-input bg-white text-lg font-bold text-slate hover:bg-line-soft"
          >
            ×
          </div>
        </div>
        <div className="flex flex-1 flex-col gap-[18px] overflow-auto px-[22px] py-5">
          <div>
            <div className="mb-2.5 text-sm font-extrabold text-navy">알람 정보</div>
            <div className="grid grid-cols-3 gap-2.5">
              {sel &&
                [
                  { k: 'LOT', v: sel.lot_id },
                  { k: 'WAFER', v: 'w' + sel.wafer_no },
                  { k: '챔버', v: sel.chamber_id },
                  { k: '센서', v: sel.sensor_id },
                  { k: 'RECIPE STEP', v: sel.recipe_step_name },
                  { k: '규칙', v: sel.rule_id },
                ].map((f) => (
                  <div key={f.k} className="rounded-lg bg-page px-3 py-[9px]">
                    <div className="text-[11.5px] font-bold text-slate-light">{f.k}</div>
                    <div className="mt-0.5 font-mono text-sm font-extrabold text-navy">{f.v}</div>
                  </div>
                ))}
            </div>
            {sel && (
              <div
                className="mt-2.5 rounded-lg border px-3.5 py-[11px] font-mono text-[13.5px] font-bold"
                style={{
                  background: isConsec ? '#FEE2E2' : sel.judgement === 'OOC' ? '#FEF3C7' : '#FEF2F2',
                  borderColor: isConsec ? '#FCA5A5' : sel.judgement === 'OOC' ? '#FDE68A' : '#FECACA',
                  color: sel.judgement === 'OOC' ? '#D97706' : '#DC2626',
                }}
              >
                {sel.detail}
              </div>
            )}
          </div>
          <div>
            <div className="mb-2.5 text-sm font-extrabold text-navy">Agent 분석 결과</div>
            {sel && sel.alarm_id === 'ALM-0008' ? (
              <div className="flex flex-col gap-2.5 rounded-[10px] border border-[#BFDBFE] bg-[#F0F6FF] px-4 py-3.5">
                <div className="flex items-center gap-2.5">
                  <span className="rounded-md bg-brand px-2.5 py-1 font-mono text-xs font-extrabold text-white">
                    FAULT CODE: RFM
                  </span>
                  <span className="text-sm font-extrabold text-navy">RF 정합 이상</span>
                </div>
                <div className="text-sm font-medium leading-[1.55] text-ink">
                  ET_REFL(반사파) 연속 3 WAFER OOS — RF 정합 상태 불량으로 판단.
                  <br />
                  동일 챔버(ETC-01-C2) 반복 발생으로 챔버 하드웨어 점검 필요.
                </div>
                <div className="flex items-center gap-2.5 border-t border-[#DBEAFE] pt-2">
                  <span className="text-[13px] font-bold text-slate">권고 조치</span>
                  <span className="rounded-md bg-navy px-3 py-1 font-mono text-[13px] font-extrabold text-white">
                    EQP_HOLD
                  </span>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-ooc-soft px-2.5 py-1 text-xs font-extrabold text-ooc">
                    <span className="h-1.5 w-1.5 animate-[om-pulse_1.6s_infinite] rounded-full bg-ooc" />
                    승인 필요
                  </span>
                </div>
              </div>
            ) : (
              <div className="rounded-[10px] border border-dashed border-line-input bg-page p-4 text-center text-[13.5px] font-semibold text-slate">
                이 알람의 Agent 분석 결과 실측 데이터가 아직 제공되지 않았습니다
              </div>
            )}
          </div>
          <div>
            <div className="mb-2.5 text-sm font-extrabold text-navy">근거</div>
            <div className="flex flex-col gap-2">
              <div className="overflow-hidden rounded-[10px] border border-line">
                <div
                  onClick={() => toggleAcc(1)}
                  className="flex cursor-pointer items-center gap-2.5 bg-page px-4 py-3 text-[13.5px] font-extrabold text-navy hover:bg-line-soft"
                >
                  <span>① 센서 요약</span>
                  <span className="ml-auto text-xs text-slate">{acc[1] ? '▲' : '▼'}</span>
                </div>
                {acc[1] && sel && (
                  <div className="grid grid-cols-4 gap-2.5 px-4 py-3.5">
                    {[
                      ['mean', numFrom(sel.detail, /mean ([\d.]+)/), '#0F2A5C'],
                      ['min', numFrom(sel.detail, /min ([\d.]+)/), '#0F2A5C'],
                      ['max', numFrom(sel.detail, /max ([\d.]+)/), '#0F2A5C'],
                      ['hit_cnt', sel.hit_cnt, '#DC2626'],
                    ].map(([k, v, color]) => (
                      <div key={k} className="text-center">
                        <div className="text-[11.5px] font-bold text-slate-light">{k}</div>
                        <div className="font-mono text-[17px] font-extrabold" style={{ color }}>
                          {v}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="overflow-hidden rounded-[10px] border border-line">
                <div
                  onClick={() => toggleAcc(2)}
                  className="flex cursor-pointer items-center gap-2.5 bg-page px-4 py-3 text-[13.5px] font-extrabold text-navy hover:bg-line-soft"
                >
                  <span>② 장비 관계 (상류/하류)</span>
                  <span className="ml-auto text-xs text-slate">{acc[2] ? '▲' : '▼'}</span>
                </div>
                {acc[2] && (
                  <div className="m-2.5 rounded-lg border border-dashed border-line-input bg-page px-4 py-3.5 text-center text-[13.5px] font-semibold text-slate">
                    관계 실측 데이터 미제공 — 화면 5(관계·문서 근거)용 값을 주시면 채웁니다
                  </div>
                )}
              </div>
              <div className="overflow-hidden rounded-[10px] border border-line">
                <div
                  onClick={() => toggleAcc(3)}
                  className="flex cursor-pointer items-center gap-2.5 bg-page px-4 py-3 text-[13.5px] font-extrabold text-navy hover:bg-line-soft"
                >
                  <span>③ 문서 근거</span>
                  <span className="ml-auto text-xs text-slate">{acc[3] ? '▲' : '▼'}</span>
                </div>
                {acc[3] && (
                  <div className="m-2.5 rounded-lg border border-dashed border-line-input bg-page px-4 py-3.5 text-center text-[13.5px] font-semibold text-slate">
                    문서 실측 데이터 미제공 — 문서명·절 제목을 주시면 채웁니다
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

export default AlarmsPage
