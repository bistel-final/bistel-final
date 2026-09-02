import { NavLink, Outlet } from 'react-router-dom'
import { PRIMARY_MENUS } from './navigation.js'

// 회사 포털의 흰색·청회색 톤을 반영한 공통 셸 — 라이트 사이드바 232px sticky, 네비 7개.
// 트레이스 뷰어 · 조치 목록은 네비와 라우트 모두에서 제외한다 (팀 합의).

function Layout() {
  return (
    <div className="flex min-h-screen min-w-[1620px] items-stretch bg-bg text-[13px] text-ink">
      <nav className="sticky top-0 flex h-screen w-[232px] flex-none flex-col self-start border-r border-line bg-white text-ink shadow-[2px_0_16px_rgba(44,92,134,0.06)]">
        <div className="flex items-center px-[18px] pb-6 pt-[22px]">
          {/* #260 로고 단순화는 유지하고 #269 라이트 셸의 네이비 텍스트를 적용한다. */}
          <div className="text-[17px] font-extrabold leading-tight tracking-[-.01em] text-navy">Photo Etch</div>
        </div>
        <div className="flex flex-col gap-0.5 px-2.5">
          {PRIMARY_MENUS.map((m, i) => (
            <NavLink
              key={m.to}
              to={m.to}
              className={({ isActive }) =>
                `relative flex h-10 items-center gap-3 rounded-lg px-3 text-[13px] no-underline hover:no-underline ${
                  isActive
                    ? 'bg-tint-blue font-bold text-blue hover:text-blue'
                    : 'text-g1 hover:bg-soft hover:text-navy'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute left-0 top-[7px] h-[26px] w-[3px] rounded-r bg-accent-bar" />
                  )}
                  <span className={`w-3 flex-none font-mono text-[11px] ${isActive ? 'text-blue' : 'text-faint'}`}>
                    {i + 1}
                  </span>
                  <span>{m.label}</span>
                </>
              )}
            </NavLink>
          ))}
        </div>
        <div className="mt-auto flex items-center gap-2 border-t border-cell-line px-[22px] py-5 text-xs text-g2">
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
