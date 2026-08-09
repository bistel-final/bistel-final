import { createBrowserRouter, Navigate } from 'react-router-dom'
import Layout from './Layout.jsx'
import DashboardPage from '../features/detection/pages/DashboardPage.jsx'
import AlarmsPage from '../features/detection/pages/AlarmsPage.jsx'
import TracePage from '../features/detection/pages/TracePage.jsx'
import AgentRunPage from '../features/agent/pages/AgentRunPage.jsx'
import ActionsPage from '../features/agent/pages/ActionsPage.jsx'
import AnalyticsPage from '../features/analytics/pages/AnalyticsPage.jsx'
import AuditLogPage from '../features/analytics/pages/AuditLogPage.jsx'

// 승인 대기 건이 있는 런이 Agent 화면 기본 진입점 (지시서 §3-3 대상)
const DEFAULT_RUN_ID = 'RUN-20260603-0005'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: 'dashboard', element: <DashboardPage /> },
      // 선택 알람이 URL에 담긴다 — 새로고침·링크 공유 시 상세 패널까지 복원
      { path: 'alarms', element: <AlarmsPage /> },
      { path: 'alarms/:alarmId', element: <AlarmsPage /> },
      // 트레이스 필터는 쿼리스트링에 직렬화
      { path: 'traces', element: <TracePage /> },
      { path: 'agent-runs', element: <Navigate to={`/agent-runs/${DEFAULT_RUN_ID}`} replace /> },
      { path: 'agent-runs/:runId', element: <AgentRunPage /> },
      { path: 'actions', element: <ActionsPage /> },
      { path: 'analytics', element: <AnalyticsPage /> },
      { path: 'audit-logs', element: <AuditLogPage /> },
      { path: '*', element: <Navigate to="/dashboard" replace /> },
    ],
  },
])
