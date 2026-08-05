import { useCallback, useEffect, useState } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  ReferenceLine,
  ReferenceArea,
  ResponsiveContainer,
} from 'recharts'
import { getAlarms, getTrace } from '../../../shared/api/detection.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'

const REF_STYLE = {
  USL: { color: '#DC2626', width: 3, dash: '7 5' },
  LSL: { color: '#DC2626', width: 3, dash: '7 5' },
  UCL: { color: '#D97706', width: 2, dash: '6 4' },
  LCL: { color: '#D97706', width: 2, dash: '6 4' },
  TARGET: { color: '#94A3B8', width: 2, dash: undefined },
}

// 포인트 상태: USL(60) 초과 OOS, UCL(36) 초과 OOC
const pointOf = (v, i) => {
  const oos = v > 60
  const ooc = !oos && v > 36
  return {
    x: 8.33 + i * 16.67,
    y: v,
    n: i + 1,
    color: oos ? '#DC2626' : ooc ? '#D97706' : '#1E5FC2',
    size: oos ? 18 : ooc ? 16 : 13,
  }
}

const TraceDot = (props) => {
  const { cx, cy, payload } = props
  return (
    <g key={`pt-${payload.n}`}>
      <circle cx={cx} cy={cy} r={payload.size / 2 + 2} fill="none" stroke={payload.color} strokeWidth={2} />
      <circle cx={cx} cy={cy} r={payload.size / 2} fill={payload.color} stroke="#FFFFFF" strokeWidth={3} />
      <text
        x={cx}
        y={cy - payload.size / 2 - 10}
        textAnchor="middle"
        fill={payload.color}
        fontSize={12.5}
        fontWeight={800}
        fontFamily="ui-monospace, SF Mono, Menlo, monospace"
      >
        {payload.y.toFixed(1)}
      </text>
    </g>
  )
}

const JUD_COLOR = { OOS: ['#FEE2E2', '#DC2626'], OOC: ['#FEF3C7', '#D97706'] }

function TracePage() {
  const [alarms, setAlarms] = useState(null)
  const [trace, setTrace] = useState(null)
  const [error, setError] = useState(null)
  const [lot, setLot] = useState('LOT-260008')
  const [wafer, setWafer] = useState('1')
  const [sensor, setSensor] = useState('PH_FOCUS')

  const load = useCallback(() => {
    Promise.all([getAlarms(), getTrace('LH-00141', 'PH_FOCUS')])
      .then(([a, t]) => {
        setAlarms(a)
        setTrace(t)
      })
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  if (error) return <ErrorState detail={error} onRetry={() => { setError(null); load() }} />
  if (!alarms || !trace) return <LoadingState message="Trace 데이터를 불러오는 중…" />

  const lots = [...new Set(alarms.map((a) => a.lot_id))]
  const wafers = [...new Set(alarms.filter((a) => a.lot_id === lot).map((a) => a.wafer_no))].sort((a, b) => a - b)
  const known = lot === trace.known.lot && wafer === trace.known.wafer && sensor === trace.known.sensor
  const title = `${lot} · W${wafer} · ${sensor}`
  const points = trace.points.map(pointOf)
  const gaugePct = trace.anomalyScore * 100
  const thresholdPct = trace.anomalyThreshold * 100

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
      <div className="flex items-center gap-4">
        <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">센서 Trace</div>
        <div className="ml-auto flex items-center gap-3.5">
          {[
            { label: 'LOT', value: lot, set: setLot, opts: lots },
            { label: 'WAFER', value: wafer, set: setWafer, opts: wafers.length ? wafers : ['1'] },
            { label: '센서', value: sensor, set: setSensor, opts: trace.sensors },
          ].map((s) => (
            <div key={s.label} className="flex items-center gap-2">
              <span className="text-[13px] font-bold text-slate">{s.label}</span>
              <select
                value={s.value}
                onChange={(e) => s.set(e.target.value)}
                className="rounded-lg border border-line-input bg-white px-2.5 py-2 font-mono text-[13.5px] font-bold text-navy"
              >
                {s.opts.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      </div>
      {sensor === 'ET_REFL' && (
        <div className="inline-flex items-center gap-2 self-start rounded-lg border border-[#FDE68A] bg-ooc-soft px-3.5 py-[9px] text-[13.5px] font-bold text-ooc">
          ET_REFL은 하한선 없이 상한 기준선 2개(USL·UCL)만 적용됩니다
        </div>
      )}
      <div className="grid grid-cols-[1fr_320px] items-start gap-4">
        <div className="rounded-xl border border-line bg-white px-[22px] py-5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <div className="mb-3.5 flex items-center gap-3">
            <div className="text-base font-extrabold text-navy">
              Trace <span className="font-mono text-[13.5px] font-bold text-slate">{title}</span>
            </div>
            <span className="ml-auto text-xs font-bold text-slate">RECIPE STEP당 3포인트 · 총 6포인트 · 단위 nm</span>
          </div>
          {known ? (
            <>
              <div className="h-[460px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={points} margin={{ top: 28, right: 48, bottom: 8, left: 0 }}>
                    <ReferenceArea
                      x1={0}
                      x2={50}
                      fill="rgba(46,123,232,.045)"
                      label={{ value: 'EXPOSE', position: 'insideTopLeft', fill: '#1E5FC2', fontSize: 12, fontWeight: 800 }}
                    />
                    <ReferenceArea
                      x1={50}
                      x2={100}
                      fill="rgba(53,181,200,.06)"
                      label={{ value: 'DEVELOP', position: 'insideTopLeft', fill: '#0E7490', fontSize: 12, fontWeight: 800 }}
                    />
                    <ReferenceLine x={50} stroke="#CBD5E1" strokeDasharray="4 4" />
                    {trace.refLines.map((rl) => {
                      const st = REF_STYLE[rl.label]
                      return (
                        <ReferenceLine
                          key={rl.label}
                          y={rl.value}
                          stroke={st.color}
                          strokeWidth={st.width}
                          strokeDasharray={st.dash}
                          label={{
                            value: rl.label,
                            position: 'right',
                            fill: st.color === '#94A3B8' ? '#64748B' : st.color,
                            fontSize: 11.5,
                            fontWeight: 800,
                            fontFamily: 'ui-monospace, SF Mono, Menlo, monospace',
                          }}
                        />
                      )
                    })}
                    <XAxis
                      dataKey="x"
                      type="number"
                      domain={[0, 100]}
                      ticks={points.map((p) => p.x)}
                      tickFormatter={(x) => `P${points.findIndex((p) => p.x === x) + 1}`}
                      axisLine={false}
                      tickLine={false}
                      tick={{ fill: '#475569', fontSize: 12, fontWeight: 700, fontFamily: 'ui-monospace, Menlo, monospace' }}
                    />
                    <YAxis
                      type="number"
                      domain={trace.yDomain}
                      ticks={trace.refLines.map((r) => r.value)}
                      tickFormatter={(v) => v.toFixed(1)}
                      axisLine={false}
                      tickLine={false}
                      width={44}
                      tick={{ fill: '#64748B', fontSize: 11.5, fontWeight: 700, fontFamily: 'ui-monospace, Menlo, monospace' }}
                    />
                    <Line
                      dataKey="y"
                      stroke="#1E5FC2"
                      strokeWidth={3.5}
                      dot={TraceDot}
                      isAnimationActive
                      animationDuration={1300}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div className="mt-1.5 flex flex-wrap gap-4 text-[12.5px] font-bold text-slate">
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full bg-oos" />
                  OOS (규격 이탈)
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full bg-ooc" />
                  OOC (UCL 초과)
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full bg-brand" />
                  정상 범위
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-[22px] border-t-[3px] border-dashed border-oos" />
                  USL/LSL
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-[22px] border-t-2 border-dashed border-ooc" />
                  UCL/LCL
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-[22px] border-t-2 border-[#94A3B8]" />
                  TARGET
                </span>
              </div>
            </>
          ) : (
            <div className="flex h-[440px] flex-col items-center justify-center gap-2.5 rounded-[10px] border-2 border-dashed border-line-input bg-page p-6 text-center">
              <div className="text-base font-extrabold text-navy">이 조합의 Trace 실측값이 아직 없습니다</div>
              <div className="text-sm font-semibold leading-[1.6] text-slate">
                현재 실측 제공: LOT-260008 · WAFER 1 · PH_FOCUS
                <br />
                다른 조합의 기준선·포인트 값을 주시면 추가합니다.
              </div>
            </div>
          )}
        </div>
        <div className="flex flex-col gap-3.5 rounded-xl border border-line bg-white px-[22px] py-5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <div className="font-mono text-[15px] font-extrabold text-navy">{title}</div>
          {known ? (
            <>
              {trace.stepStats.map((st) => {
                const jc = JUD_COLOR[st.jud]
                return (
                  <div key={st.name} className="rounded-[10px] border border-line px-3.5 py-3">
                    <div className="mb-2.5 flex items-center gap-2">
                      <span className="font-mono text-[13px] font-extrabold text-navy">{st.name}</span>
                      <span
                        className="ml-auto rounded-md px-[9px] py-[3px] font-mono text-[11.5px] font-extrabold"
                        style={{ background: jc[0], color: jc[1] }}
                      >
                        {st.jud}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      {[
                        ['mean', st.mean],
                        ['std', st.std],
                        ['min', st.min],
                        ['max', st.max],
                      ].map(([k, v]) => (
                        <div key={k} className="rounded-md bg-page px-2.5 py-1.5">
                          <span className="text-[11px] font-bold text-slate-light">{k}</span>
                          <div className="font-mono text-[15px] font-extrabold text-navy">{v}</div>
                        </div>
                      ))}
                    </div>
                    <div className="mt-2.5 flex gap-3.5 text-[13px] font-bold">
                      <span className="text-ooc">OOC {st.ooc}</span>
                      <span className="text-oos">OOS {st.oos}</span>
                      <span className="ml-auto text-slate">{st.pts}포인트</span>
                    </div>
                  </div>
                )
              })}
              <div className="rounded-[10px] border border-[#FECACA] bg-[#FEF2F2] p-3.5">
                <div className="flex items-baseline gap-2">
                  <span className="text-[13px] font-extrabold text-navy">anomaly_score</span>
                  <span className="ml-auto font-mono text-[26px] font-extrabold text-oos">
                    {trace.anomalyScore.toFixed(2)}
                  </span>
                </div>
                <div className="relative mt-[30px] h-2.5 rounded-[5px] bg-line">
                  <div
                    className="absolute bottom-0 left-0 top-0 rounded-[5px] bg-[linear-gradient(90deg,#D97706,#DC2626)]"
                    style={{ width: `${gaugePct}%` }}
                  />
                  <div
                    className="absolute -top-2 -bottom-1 w-[2.5px] bg-navy"
                    style={{ left: `${thresholdPct}%` }}
                  />
                  <div
                    className="absolute -top-6 -translate-x-1/2 whitespace-nowrap font-mono text-[11px] font-extrabold text-navy"
                    style={{ left: `${thresholdPct}%` }}
                  >
                    임계 {trace.anomalyThreshold.toFixed(2)}
                  </div>
                </div>
                <div className="mt-2.5 text-[12.5px] font-bold text-oos">
                  임계값 {trace.anomalyThreshold.toFixed(2)} 초과 — 이상 판정
                </div>
              </div>
            </>
          ) : (
            <div className="rounded-[10px] border border-dashed border-line-input bg-page p-5 text-center text-[13.5px] font-semibold text-slate">
              요약 실측값 미제공
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default TracePage
