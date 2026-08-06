import { NavLink, Outlet } from 'react-router-dom'

// 사이드바 7메뉴 — 담당자(C) 인수 영역이므로 최소 구성 유지
// 경로는 시스템설계서 12.1 라우트 계약을 따른다.
const MENUS = [
  { to: '/', label: '운영 대시보드', end: true },
  { to: '/alarms', label: '알람 목록' },
  { to: '/traces', label: '센서 Trace' },
  { to: '/approvals', label: 'Agent 분석·승인' },
  { to: '/relations', label: '관계·문서 근거' },
  { to: '/analytics', label: '자연어 분석' },
  { to: '/audit-logs', label: '감사로그' },
]

const HEADER_DOTS = ['DB', 'Neo4j', 'n8n']

function Layout() {
  return (
    <div className="flex h-screen min-w-[1440px] bg-page text-[15px] text-ink">
      <aside className="flex w-[236px] flex-none flex-col bg-navy text-white">
        <div className="border-b border-white/12 px-5 pt-[22px] pb-[18px]">
          <div className="text-[21px] font-extrabold tracking-[-.3px]">
            <span className="text-white">BISTel</span>
            <span className="text-teal">ligence</span>
          </div>
          <div className="mt-1 text-[11.5px] tracking-[.4px] text-sidebar-dim">
            FDC ANOMALY AGENT PLATFORM
          </div>
        </div>
        <nav className="flex flex-col gap-0.5 px-2.5 py-3.5">
          {MENUS.map((m, i) => (
            <NavLink
              key={m.to}
              to={m.to}
              end={m.end}
              className={({ isActive }) =>
                `flex cursor-pointer items-center gap-[11px] rounded-lg border-l-[3px] py-2.5 pl-[9px] pr-3 no-underline transition-colors duration-150 hover:no-underline ${
                  isActive
                    ? 'border-teal bg-brand-light font-extrabold text-white hover:text-white'
                    : 'border-transparent font-semibold text-white/70 hover:bg-white/10 hover:text-white'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={`flex h-[22px] w-[22px] flex-none items-center justify-center rounded-md text-[11.5px] font-bold ${
                      isActive ? 'bg-white/22 text-white' : 'bg-white/8 text-sidebar-dim'
                    }`}
                  >
                    {i + 1}
                  </span>
                  <span className="text-[14.5px]">{m.label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto border-t border-white/12 px-5 py-4 text-xs text-sidebar-dim">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 animate-[om-pulse_2s_infinite] rounded-full bg-[#4ADE80]" />
            Agent 파이프라인 가동 중
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-[60px] flex-none items-center gap-4 border-b border-line bg-white px-7">
          <div className="text-[17px] font-extrabold tracking-[-.2px] text-navy">BISTEL Final</div>
          <div className="ml-auto flex items-center gap-5">
            <div className="text-[13.5px] text-slate">
              기준일 <span className="font-mono font-bold text-navy">2026-06-04</span>
            </div>
            <div className="h-[22px] w-px bg-line" />
            <div className="flex items-center gap-3.5 text-[12.5px] font-semibold text-slate">
              {HEADER_DOTS.map((label, i) => (
                <span key={label} className="flex items-center gap-1.5">
                  <span
                    className="h-[9px] w-[9px] animate-[om-pulse_2.4s_infinite] rounded-full bg-ok"
                    style={{ animationDelay: `${i * 0.4}s` }}
                  />
                  {label}
                </span>
              ))}
            </div>
          </div>
        </header>
        <main className="flex-1 overflow-auto px-7 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export default Layout
