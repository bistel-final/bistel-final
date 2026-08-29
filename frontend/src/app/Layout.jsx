import { NavLink, Outlet } from 'react-router-dom'
import { PRIMARY_MENUS } from './navigation.js'

// 라이트 테마 개편 공통 셸 — 네이비 사이드바 232px sticky, 네비 7개, 상단 헤더 바 없음.
// 트레이스 뷰어 · 조치 목록은 네비와 라우트 모두에서 제외한다 (팀 합의).

function Layout() {
  return (
    <div className="flex min-h-screen min-w-[1620px] items-stretch bg-bg text-[13px] text-ink">
      <nav className="sticky top-0 flex h-screen w-[232px] flex-none flex-col self-start bg-navy text-white">
        <div className="flex items-center gap-2.5 px-[18px] pb-6 pt-[22px]">
          <div className="flex h-[34px] w-[34px] flex-none items-center justify-center rounded-[9px] bg-blue text-[13px] font-extrabold text-white">
            PE
          </div>
          <div className="min-w-0">
            <div className="text-[17px] font-extrabold leading-tight tracking-[-.01em] text-white">Photo Etch</div>
            <div className="mt-0.5 text-[8px] tracking-[.16em] text-side-dim">FDC ANOMALY AGENT PLATFORM</div>
          </div>
        </div>
        <div className="flex flex-col gap-0.5 px-2.5">
          {PRIMARY_MENUS.map((m, i) => (
            <NavLink
              key={m.to}
              to={m.to}
              className={({ isActive }) =>
                `relative flex h-10 items-center gap-3 rounded-lg px-3 text-[13px] no-underline hover:no-underline ${
                  isActive
                    ? 'bg-navy2 font-bold text-white hover:text-white'
                    : 'text-side-text hover:bg-white/6 hover:text-white'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute left-0 top-[7px] h-[26px] w-[3px] rounded-r bg-accent-bar" />
                  )}
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
      <main className="min-w-0 flex-1 px-[26px] py-[22px]">
        <Outlet />
      </main>
    </div>
  )
}

export default Layout
