import { NavLink, Outlet } from 'react-router-dom'

const navigation = [
  { to: '/dashboard', label: '운영 대시보드' },
  { to: '/alarms', label: '알람 목록' },
  { to: '/relations', label: '관계·문서 근거' },
  { to: '/approvals', label: '승인 대기 큐' },
  { to: '/analytics', label: '자연어 분석' },
  { to: '/audit-logs', label: '감사로그' },
]

function AppLayout() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">FDC</span>
          <div>
            <strong>BISTel Agent</strong>
            <small>반도체 이상감지 플랫폼</small>
          </div>
        </div>

        <nav className="navigation" aria-label="주요 화면">
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                isActive ? 'navigation-link active' : 'navigation-link'
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="app-main">
        <header className="app-header">
          <div>
            <p className="header-eyebrow">LangGraph 기반</p>
            <h1>반도체 FDC 이상감지 에이전트</h1>
          </div>
          <span className="environment-badge">Development</span>
        </header>

        <main className="app-content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export default AppLayout
