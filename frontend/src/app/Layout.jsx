import { NavLink, Outlet } from 'react-router-dom'

// 디자인 v2 Sidebar.dc.html — 7메뉴, 하단 가동 인디케이터. 상단 헤더 바 없음.
const MENUS = [
  { to: '/dashboard', label: '알람 대시보드' },
  { to: '/alarms', label: '알람 목록' },
  { to: '/traces', label: '트레이스 뷰어' },
  { to: '/agent-runs', label: 'Agent 분석 · 승인' },
  { to: '/actions', label: '조치 목록' },
  { to: '/analytics', label: '자연어 분석' },
  { to: '/audit-logs', label: '감사로그' },
]

function Layout() {
  return (
    <div className="flex min-h-screen min-w-[1620px] items-stretch bg-bg text-[13px] text-ink">
      <nav className="flex w-[236px] flex-none flex-col bg-navy text-white">
        <div className="px-[22px] pb-6 pt-[26px]">
          <div className="text-[19px] font-extrabold tracking-[-.01em]">BISTelligence</div>
          <div className="mt-1 font-mono text-[9px] tracking-[.09em] text-side-dim">
            FDC ANOMALY AGENT PLATFORM
          </div>
        </div>
        <div className="flex flex-col gap-0.5 px-2.5">
          {MENUS.map((m, i) => (
            <NavLink
              key={m.to}
              to={m.to}
              className={({ isActive }) =>
                `flex h-10 items-center gap-3 rounded-lg px-3 text-[13.5px] no-underline hover:no-underline ${
                  isActive
                    ? 'bg-navy2 font-bold text-white hover:text-white'
                    : 'text-side-text hover:bg-white/6 hover:text-white'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <span className={`w-3 flex-none font-mono text-[11px] ${isActive ? 'text-side-num-on' : 'text-side-num'}`}>
                    {i + 1}
                  </span>
                  <span>{m.label}</span>
                </>
              )}
            </NavLink>
          ))}
        </div>
        <div className="mt-auto flex items-center gap-2 px-[22px] py-5 text-xs text-side-foot">
          <span className="h-2 w-2 flex-none rounded-full bg-dot-green" />
          <span>Agent 파이프라인 가동 중</span>
        </div>
      </nav>
      <main className="min-w-0 flex-1 px-7 pb-7">
        <Outlet />
      </main>
    </div>
  )
}

export default Layout
