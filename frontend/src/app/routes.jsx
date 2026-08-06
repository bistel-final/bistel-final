import { createBrowserRouter, Navigate } from 'react-router-dom'
import Layout from './Layout.jsx'
import DashboardPage from '../features/detection/pages/DashboardPage.jsx'
import AlarmsPage from '../features/detection/pages/AlarmsPage.jsx'
import TracePage from '../features/detection/pages/TracePage.jsx'
import AgentPage from '../features/agent/pages/AgentPage.jsx'
import KnowledgePage from '../features/knowledge/pages/KnowledgePage.jsx'
import AnalyticsPage from '../features/analytics/pages/AnalyticsPage.jsx'
import AuditLogPage from '../features/analytics/pages/AuditLogPage.jsx'

// 경로는 시스템설계서 12.1 라우트 계약을 따른다.
// 상세 경로(:alarmId, :lotHistId)는 계약만 등록하고 useParams() 처리는 A 담당이다.
export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'dashboard', element: <Navigate to="/" replace /> },

      { path: 'alarms', element: <AlarmsPage /> },
      { path: 'alarms/:alarmId', element: <AlarmsPage /> },

      { path: 'traces', element: <TracePage /> },
      { path: 'traces/:lotHistId', element: <TracePage /> },

      { path: 'approvals', element: <AgentPage /> },
      { path: 'relations', element: <KnowledgePage /> },

      { path: 'analytics', element: <AnalyticsPage /> },
      { path: 'audit-logs', element: <AuditLogPage /> },

      // 레거시 경로 — 기존 링크가 깨지지 않도록 유지한다.
      { path: 'agent', element: <Navigate to="/approvals" replace /> },
      { path: 'knowledge', element: <Navigate to="/relations" replace /> },

      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
])
