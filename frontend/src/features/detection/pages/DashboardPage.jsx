import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getDashboard } from '../../../shared/api/detection.js'
import LoadingState, { Skeleton } from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'

const ALL = '전체'

const ruleBadge = (rule, crit) =>
  crit
    ? { bg: '#DC2626', color: '#FFFFFF' }
    : rule.includes('OOC')
      ? { bg: '#FEF3C7', color: '#D97706' }
      : { bg: '#FEE2E2', color: '#DC2626' }

// 눈금 3칸에 맞는 단위 선택 — 전체 스코프(최대 27건)는 dc.html과 같은 0/10/20/30이 된다
const axisStep = (max) => [1, 2, 5, 10, 20, 50, 100].find((s) => s * 3 >= max) ?? 100

function Dropdown({ label, value, options, onChange }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[13px] font-bold text-slate">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-line-input bg-white px-2.5 py-2 font-mono text-[13.5px] font-bold text-navy"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  )
}

function DashboardPage() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [date, setDate] = useState('2026-06-04')
  // 계층 필터: AREA > EQUIPMENT > CHAMBER
  const [area, setArea] = useState(ALL)
  const [equipment, setEquipment] = useState(ALL)
  const [chamber, setChamber] = useState(ALL)
  const [hoverIdx, setHoverIdx] = useState(-1)
  const [flash, setFlash] = useState(false)
  const [p, setP] = useState(0) // KPI count-up 진행도
  const rafRef = useRef()

  const load = useCallback(() => {
    getDashboard('2026-06-04', ALL)
      .then(setData)
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  // count-up: 1100ms ease-out cubic (dc.html 동작)
  useEffect(() => {
    if (!data) return
    // 기준점은 첫 프레임 타임스탬프 — performance.now()와 섞으면 시간축이 어긋나 진행도가 음수가 된다
    let t0 = null
    const step = (t) => {
      if (t0 === null) t0 = t
      const x = Math.min(1, Math.max(0, (t - t0) / 1100))
      setP(1 - Math.pow(1 - x, 3))
      if (x < 1) rafRef.current = requestAnimationFrame(step)
    }
    rafRef.current = requestAnimationFrame(step)
    // 프레임이 돌지 않는 환경에서도 최종값이 보이도록 보장
    const settle = setTimeout(() => setP(1), 1300)
    return () => {
      cancelAnimationFrame(rafRef.current)
      clearTimeout(settle)
    }
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
        <div className="grid grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-[118px]" />
          ))}
        </div>
        <Skeleton className="h-[320px]" />
      </LoadingState>
    )

  const areaGroups = area === ALL ? data.hierarchy : data.hierarchy.filter((h) => h.area === area)
  const eqpOptions = [ALL, ...areaGroups.flatMap((h) => h.equipments.map((e) => e.id))]
  const chOptions = [
    ALL,
    ...areaGroups.flatMap((h) =>
      h.equipments.filter((e) => equipment === ALL || e.id === equipment).flatMap((e) => e.chambers),
    ),
  ]
  // 가장 좁은 선택이 스코프를 결정한다
  const scopeKey = chamber !== ALL ? chamber : equipment !== ALL ? equipment : area
  const scope = data.scopes[scopeKey]
  const { kpi, days, recent } = scope

  const maxTotal = Math.max(...days.map((d) => d.oos + d.ooc), 1)
  const step = axisStep(maxTotal)
  const unitPx = 240 / (step * 3)
  const isToday = (t) => !t.includes('/')

  const changeArea = (v) => {
    setArea(v)
    setEquipment(ALL)
    setChamber(ALL)
  }
  const changeEquipment = (v) => {
    setEquipment(v)
    setChamber(ALL)
  }

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-[18px]">
      <div className="flex items-center gap-4">
        <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">알람 대시보드</div>
        <div className="ml-auto flex items-center gap-3">
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="rounded-lg border border-line-input bg-white px-3 py-2 font-mono text-sm font-semibold text-navy"
          />
          <Dropdown label="공정" value={area} options={[ALL, ...data.hierarchy.map((h) => h.area)]} onChange={changeArea} />
          <Dropdown label="장비" value={equipment} options={eqpOptions} onChange={changeEquipment} />
          <Dropdown label="챔버" value={chamber} options={chOptions} onChange={setChamber} />
        </div>
      </div>
      <div className="grid grid-cols-4 gap-4">
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
            {Math.round(scope.pending * p)}
            <span className="ml-[3px] text-[17px] font-bold text-[#D5E2F5]">건</span>
          </div>
          <div className="mt-1.5 text-[13px] font-bold text-white">Agent 분석·승인으로 이동 →</div>
        </div>
        <div className="rounded-xl border border-line bg-white px-5 py-[18px] shadow-[0_1px_3px_rgba(15,42,92,.05)] transition-[transform,box-shadow] duration-[180ms] hover:-translate-y-[3px] hover:shadow-[0_8px_20px_rgba(15,42,92,.12)]">
          <div className="text-[13.5px] font-bold text-slate">활성 조치</div>
          <div className="mt-1.5 font-mono text-4xl font-extrabold text-navy">
            {Math.round(scope.active * p)}
            <span className="ml-[3px] text-[17px] font-bold text-slate">건</span>
          </div>
          <div className="mt-1.5 text-[13px] font-semibold text-slate">진행 중 Action</div>
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
              {[1, 2, 3].map((n) => (
                <div
                  key={n}
                  className="absolute left-0 right-0 border-b border-line-soft"
                  style={{ bottom: step * n * unitPx }}
                />
              ))}
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
                      style={{ height: d.oos * unitPx, animation: `om-grow .7s ${i * 0.12}s cubic-bezier(.2,.8,.3,1) both` }}
                    />
                    <div
                      className="mb-0.5 origin-bottom rounded-t bg-teal"
                      style={{ height: d.ooc * unitPx, animation: `om-grow .7s ${i * 0.12 + 0.1}s cubic-bezier(.2,.8,.3,1) both` }}
                    />
                    {hoverIdx === i && (
                      <div
                        className="absolute left-1/2 z-[5] -translate-x-1/2 whitespace-nowrap rounded-lg bg-navy px-[13px] py-[9px] text-[13px] font-semibold text-white shadow-[0_6px_16px_rgba(15,42,92,.3)]"
                        style={{ bottom: (d.oos + d.ooc) * unitPx + 12 }}
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
              {[0, 1, 2, 3].map((n) => (
                <span key={n} className="absolute right-1" style={{ bottom: step * n * unitPx - 6 }}>
                  {step * n}
                </span>
              ))}
            </div>
            <div className="absolute bottom-0 left-[30px] right-0 flex h-5 justify-around font-mono text-[13px] font-bold text-slate">
              {days.map((d) => (
                <span key={d.label}>{d.label}</span>
              ))}
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
              선택한 조건에 해당하는 알람이 없습니다
            </div>
          )}
          <div className="flex flex-col">
            {recent.map((a, i) => {
              const badge = ruleBadge(a.rule, a.crit)
              return (
                <div
                  key={a.id}
                  className="grid cursor-pointer grid-cols-[92px_1fr_auto_72px] items-center gap-3 rounded-lg border-b border-line-soft px-2.5 py-[11px] transition-colors duration-150 hover:bg-line-soft"
                  style={{
                    background: a.crit ? '#FEF2F2' : 'transparent',
                    animation: i === 0 && flash && isToday(a.time) ? 'om-flash 1.8s ease-out' : 'none',
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
