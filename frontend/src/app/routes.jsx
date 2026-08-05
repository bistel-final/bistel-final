import { createBrowserRouter, Navigate } from 'react-router-dom'
import Layout from './Layout.jsx'
import DashboardPage from '../features/detection/pages/DashboardPage.jsx'
import AlarmsPage from '../features/detection/pages/AlarmsPage.jsx'
import TracePage from '../features/detection/pages/TracePage.jsx'
import AgentPage from '../features/agent/pages/AgentPage.jsx'
import KnowledgePage from '../features/knowledge/pages/KnowledgePage.jsx'
import AnalyticsPage from '../features/analytics/pages/AnalyticsPage.jsx'
import AuditLogPage from '../features/analytics/pages/AuditLogPage.jsx'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: 'dashboard', element: <DashboardPage /> },
      { path: 'alarms', element: <AlarmsPage /> },
      { path: 'traces', element: <TracePage /> },
      { path: 'agent', element: <AgentPage /> },
      { path: 'knowledge', element: <KnowledgePage /> },
      { path: 'analytics', element: <AnalyticsPage /> },
      { path: 'audit-logs', element: <AuditLogPage /> },
      { path: '*', element: <Navigate to="/dashboard" replace /> },
    ],
  },
])
