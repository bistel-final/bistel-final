import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getDashboard } from '../../../shared/api/detection.js'
import LoadingState, { Skeleton } from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'

const CHAMBER_STYLE = {
  NORMAL: { accent: '#16A34A', badgeBg: '#DCFCE7', badgeColor: '#16A34A', cardBg: '#FFFFFF', cardBorder: '#E2E8F0' },
  ALARM: { accent: '#D97706', badgeBg: '#FEF3C7', badgeColor: '#D97706', cardBg: '#FFFFFF', cardBorder: '#E2E8F0' },
  CRITICAL: { accent: '#DC2626', badgeBg: '#FEE2E2', badgeColor: '#DC2626', cardBg: '#FEF5F5', cardBorder: '#FECACA' },
}

const ruleBadge = (rule, crit) =>
  crit
    ? { bg: '#DC2626', color: '#FFFFFF' }
    : rule.includes('OOC')
      ? { bg: '#FEF3C7', color: '#D97706' }
      : { bg: '#FEE2E2', color: '#DC2626' }

function DashboardPage() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [date, setDate] = useState('2026-06-04')
  const [area, setArea] = useState('전체')
  const [hoverIdx, setHoverIdx] = useState(-1)
  const [flash, setFlash] = useState(false)
  const [p, setP] = useState(0) // KPI count-up 진행도
  const rafRef = useRef()

  const load = useCallback(() => {
    getDashboard('2026-06-04', '전체')
      .then(setData)
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  // count-up: 1100ms ease-out cubic (dc.html 동작)
  useEffect(() => {
    if (!data) return
    const t0 = performance.now()
    const step = (t) => {
      const x = Math.min(1, (t - t0) / 1100)
      setP(1 - Math.pow(1 - x, 3))
      if (x < 1) rafRef.current = requestAnimationFrame(step)
    }
    rafRef.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(rafRef.current)
  }, [data])

  // 최근 알람 첫 행 LIVE flash (7초 주기)
  useEffect(() => {
    let off
    const live = setInterval(() => {
      setFlash(true)
      off = setTimeout(() => setFlash(false), 1900)
    }, 7000)
    return () => {
      clearInterval(live)
      clearTimeout(off)
    }
  }, [])

  if (error) return <ErrorState detail={error} onRetry={() => { setError(null); load() }} />
  if (!data)
    return (
      <LoadingState message="대시보드 데이터를 불러오는 중…">
        <div className="grid grid-cols-5 gap-4">
          {[1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-[118px]" />
          ))}
        </div>
        <Skeleton className="h-[320px]" />
      </LoadingState>
    )

  const kpi = data.kpiByArea[area]
  const days = data.dayByArea[area]
  const chambers = data.chambers.filter((c) => area === '전체' || c.area === area)
  const recent = data.recentByArea[area]

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-[18px]">
      <div className="flex items-center gap-4">
        <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">운영 대시보드</div>
        <div className="ml-auto flex items-center gap-3">
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="rounded-lg border border-line-input bg-white px-3 py-2 font-mono text-sm font-semibold text-navy"
          />
          <div className="flex overflow-hidden rounded-lg border border-line-input bg-white">
            {data.areas.map((a) => (
              <div
                key={a}
                onClick={() => setArea(a)}
                className="cursor-pointer px-[18px] py-2 text-sm font-bold transition-colors duration-150"
                style={area === a ? { background: '#1E5FC2', color: '#FFFFFF' } : { background: '#FFFFFF', color: '#475569' }}
              >
                {a}
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="grid grid-cols-5 gap-4">
        <div className="rounded-xl border border-line bg-white px-5 py-[18px] shadow-[0_1px_3px_rgba(15,42,92,.05)] transition-[transform,box-shadow] duration-[180ms] hover:-translate-y-[3px] hover:shadow-[0_8px_20px_rgba(15,42,92,.12)]">
          <div className="text-[13.5px] font-bold text-slate">
            당일 알람 <span className="font-mono">(6/4)</span>
          </div>
          <div className="mt-1.5 font-mono text-4xl font-extrabold text-navy">
            {Math.round(kpi.today * p)}
            <span className="ml-[3px] text-[17px] font-bold text-slate">건</span>
          </div>
          <div className="mt-1.5 text-[13px] font-semibold">
            <span className="text-oos">OOS {kpi.todayOos}</span>
            <span className="mx-1.5 text-[#94A3B8]">·</span>
            <span className="text-ooc">OOC {kpi.todayOoc}</span>
          </div>
        </div>
        <div className="rounded-xl border border-line bg-white px-5 py-[18px] shadow-[0_1px_3px_rgba(15,42,92,.05)] transition-[transform,box-shadow] duration-[180ms] hover:-translate-y-[3px] hover:shadow-[0_8px_20px_rgba(15,42,92,.12)]">
          <div className="text-[13.5px] font-bold text-slate">전체 알람</div>
          <div className="mt-1.5 font-mono text-4xl font-extrabold text-navy">
            {Math.round(kpi.total * p)}
            <span className="ml-[3px] text-[17px] font-bold text-slate">건</span>
          </div>
          <div className="mt-1.5 text-[13px] font-semibold">
            <span className="text-oos">OOS {kpi.totalOos}</span>
            <span className="mx-1.5 text-[#94A3B8]">·</span>
            <span className="text-ooc">OOC {kpi.totalOoc}</span>
          </div>
        </div>
        <div
          onClick={() => navigate('/agent')}
          className="cursor-pointer rounded-xl border border-brand bg-[linear-gradient(160deg,#1E5FC2,#2E7BE8)] px-5 py-[18px] shadow-[0_4px_12px_rgba(30,95,194,.3)] transition-[transform,box-shadow] duration-[180ms] hover:-translate-y-[3px] hover:shadow-[0_10px_24px_rgba(30,95,194,.4)]"
        >
          <div className="flex items-center gap-1.5 text-[13.5px] font-bold text-[#D5E2F5]">
            <span className="h-[7px] w-[7px] animate-[om-pulse_1.6s_infinite] rounded-full bg-[#FBBF24]" />
            승인 대기
          </div>
          <div className="mt-1.5 font-mono text-4xl font-extrabold text-white">
            {Math.round(data.pending * p)}
            <span className="ml-[3px] text-[17px] font-bold text-[#D5E2F5]">건</span>
          </div>
          <div className="mt-1.5 text-[13px] font-bold text-white">Agent 분석·승인으로 이동 →</div>
        </div>
        <div className="rounded-xl border border-line bg-white px-5 py-[18px] shadow-[0_1px_3px_rgba(15,42,92,.05)] transition-[transform,box-shadow] duration-[180ms] hover:-translate-y-[3px] hover:shadow-[0_8px_20px_rgba(15,42,92,.12)]">
          <div className="text-[13.5px] font-bold text-slate">계측 PASS율</div>
          <div className="mt-1.5 font-mono text-4xl font-extrabold text-ok">
            {(data.passRate * p).toFixed(1)}
            <span className="ml-[3px] text-[17px] font-bold text-slate">%</span>
          </div>
          <div className="mt-1.5 font-mono text-[13px] font-semibold text-slate">{data.passMeta}</div>
        </div>
        <div className="rounded-xl border border-line bg-white px-5 py-[18px] shadow-[0_1px_3px_rgba(15,42,92,.05)] transition-[transform,box-shadow] duration-[180ms] hover:-translate-y-[3px] hover:shadow-[0_8px_20px_rgba(15,42,92,.12)]">
          <div className="text-[13.5px] font-bold text-slate">활성 조치</div>
          <div className="mt-1.5 font-mono text-4xl font-extrabold text-navy">
            {Math.round(data.active * p)}
            <span className="ml-[3px] text-[17px] font-bold text-slate">건</span>
          </div>
          <div className="mt-1.5 text-[13px] font-semibold text-slate">진행 중 Action</div>
        </div>
      </div>
      <div>
        <div className="mb-2.5 text-base font-extrabold text-navy">챔버 상태</div>
        <div className="grid grid-cols-4 gap-4">
          {chambers.map((ch) => {
            const st = CHAMBER_STYLE[ch.status]
            return (
              <div
                key={ch.name}
                className="rounded-xl border border-t-4 px-[18px] py-4 shadow-[0_1px_3px_rgba(15,42,92,.05)] transition-[transform,box-shadow] duration-[180ms] hover:-translate-y-[3px] hover:shadow-[0_8px_20px_rgba(15,42,92,.12)]"
                style={{ background: st.cardBg, borderColor: st.cardBorder, borderTopColor: st.accent }}
              >
                <div className="flex items-center gap-2.5">
                  <div className="font-mono text-[17px] font-extrabold text-navy">{ch.name}</div>
                  <span
                    className="ml-auto rounded-full px-2.5 py-1 text-[12.5px] font-extrabold tracking-[.3px]"
                    style={{ background: st.badgeBg, color: st.badgeColor }}
                  >
                    {ch.status}
                  </span>
                </div>
                <div className="mt-2.5 text-[13.5px] font-semibold text-slate">{ch.note}</div>
                {ch.hold && (
                  <div className="mt-2.5 inline-flex items-center gap-1.5 rounded-md bg-oos-soft px-2.5 py-[5px] text-[12.5px] font-extrabold text-oos">
                    <span className="h-[7px] w-[7px] animate-[om-pulse_1.4s_infinite] rounded-full bg-oos" />
                    EQP_HOLD 승인 대기
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
      <div className="grid grid-cols-[3fr_2fr] gap-4">
        <div className="rounded-xl border border-line bg-white px-[22px] py-5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <div className="mb-3.5 flex items-center gap-3.5">
            <div className="text-base font-extrabold text-navy">
              일자별 알람 추이 <span className="font-mono text-[13.5px] text-slate">(6/1–6/4)</span>
            </div>
            <div className="ml-auto flex gap-3.5 text-[12.5px] font-bold text-slate">
              <span className="flex items-center gap-1.5">
                <span className="h-[11px] w-[11px] rounded-[3px] bg-oos" />
                OOS
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-[11px] w-[11px] rounded-[3px] bg-teal" />
                OOC
              </span>
            </div>
          </div>
          <div className="relative h-[264px]">
            <div className="absolute inset-0 bottom-6 left-[30px]">
              <div className="absolute bottom-0 left-0 right-0 border-b-2 border-line-input" />
              <div className="absolute bottom-20 left-0 right-0 border-b border-line-soft" />
              <div className="absolute bottom-40 left-0 right-0 border-b border-line-soft" />
              <div className="absolute bottom-60 left-0 right-0 border-b border-line-soft" />
              <div className="absolute inset-0 flex items-end justify-around">
                {days.map((d, i) => (
                  <div
                    key={d.label}
                    onMouseEnter={() => setHoverIdx(i)}
                    onMouseLeave={() => setHoverIdx(-1)}
                    className="relative flex w-[72px] cursor-pointer flex-col-reverse items-stretch"
                  >
                    <div
                      className="origin-bottom rounded-b bg-oos"
                      style={{ height: d.oos * 8, animation: `om-grow .7s ${i * 0.12}s cubic-bezier(.2,.8,.3,1) both` }}
                    />
                    <div
                      className="mb-0.5 origin-bottom rounded-t bg-teal"
                      style={{ height: d.ooc * 8, animation: `om-grow .7s ${i * 0.12 + 0.1}s cubic-bezier(.2,.8,.3,1) both` }}
                    />
                    {hoverIdx === i && (
                      <div
                        className="absolute left-1/2 z-[5] -translate-x-1/2 whitespace-nowrap rounded-lg bg-navy px-[13px] py-[9px] text-[13px] font-semibold text-white shadow-[0_6px_16px_rgba(15,42,92,.3)]"
                        style={{ bottom: (d.oos + d.ooc) * 8 + 12 }}
                      >
                        <div className="mb-[3px] font-mono font-extrabold">
                          {d.label} · 총 {d.oos + d.ooc}건
                        </div>
                        <div>
                          <span className="text-[#FCA5A5]">OOS</span> {d.oos}{' '}
                          <span className="ml-2 text-[#7DD8E5]">OOC</span> {d.ooc}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
            <div className="absolute bottom-6 left-0 top-0 w-[26px] font-mono text-xs font-semibold text-slate-light">
              <span className="absolute -bottom-1.5 right-1">0</span>
              <span className="absolute bottom-[74px] right-1">10</span>
              <span className="absolute bottom-[154px] right-1">20</span>
              <span className="absolute bottom-[234px] right-1">30</span>
            </div>
            <div className="absolute bottom-0 left-[30px] right-0 flex h-5 justify-around font-mono text-[13px] font-bold text-slate">
              <span>6/1</span>
              <span>6/2</span>
              <span>6/3</span>
              <span>6/4</span>
            </div>
          </div>
        </div>
        <div className="flex flex-col rounded-xl border border-line bg-white px-[22px] py-5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <div className="mb-3 flex items-center">
            <div className="text-base font-extrabold text-navy">최근 알람</div>
            <span className="ml-auto flex items-center gap-1.5 text-xs font-extrabold text-ok">
              <span className="h-2 w-2 animate-[om-pulse_1.8s_infinite] rounded-full bg-ok" />
              LIVE
            </span>
          </div>
          {recent.length === 0 && (
            <div className="flex flex-1 items-center justify-center rounded-[10px] bg-page text-[14.5px] font-semibold text-slate">
              선택한 AREA에 해당하는 알람이 없습니다
            </div>
          )}
          <div className="flex flex-col">
            {recent.map((a, i) => {
              const badge = ruleBadge(a.rule, a.crit)
              return (
                <div
                  key={a.id + a.rule}
                  className="grid cursor-pointer grid-cols-[92px_1fr_auto_72px] items-center gap-3 rounded-lg border-b border-line-soft px-2.5 py-[11px] transition-colors duration-150 hover:bg-line-soft"
                  style={{
                    background: a.crit ? '#FEF2F2' : 'transparent',
                    animation: i === 0 && flash && area !== 'PHOTO' ? 'om-flash 1.8s ease-out' : 'none',
                  }}
                >
                  <span className="font-mono text-sm font-extrabold text-navy">{a.id}</span>
                  <span className="font-mono text-[13.5px] font-semibold text-ink">
                    {a.sensor}
                    <span className="font-medium text-slate-light"> · {a.eqp}</span>
                  </span>
                  <span
                    className="rounded-md px-[9px] py-1 font-mono text-xs font-extrabold"
                    style={{ background: badge.bg, color: badge.color }}
                  >
                    {a.rule}
                  </span>
                  <span className="text-right font-mono text-[13px] font-semibold text-slate">{a.time}</span>
                </div>
              )
            })}
          </div>
          <div className="mt-auto pt-3">
            <a
              href="/alarms"
              onClick={(e) => {
                e.preventDefault()
                navigate('/alarms')
              }}
              className="text-[13.5px] font-bold"
            >
              알람 목록 전체 보기 →
            </a>
          </div>
        </div>
      </div>
    </div>
  )
}

export default DashboardPage
