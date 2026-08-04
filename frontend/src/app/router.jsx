import { createBrowserRouter, Navigate } from 'react-router-dom'
import AppLayout from '../shared/components/layout/AppLayout.jsx'
import PagePlaceholder from '../shared/components/states/PagePlaceholder.jsx'
import DashboardPage from '../features/detection/pages/DashboardPage.jsx'
import AlarmListPage from '../features/detection/pages/AlarmListPage.jsx'
import TracePage from '../features/detection/pages/TracePage.jsx'
import RelationsPage from '../features/knowledge/pages/RelationsPage.jsx'
import ApprovalsPage from '../features/agent/pages/ApprovalsPage.jsx'
import AnalyticsPage from '../features/analytics/pages/AnalyticsPage.jsx'
import AuditLogsPage from '../features/analytics/pages/AuditLogsPage.jsx'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: 'dashboard', element: <DashboardPage /> },
      { path: 'alarms', element: <AlarmListPage /> },
      { path: 'traces/:lotHistId', element: <TracePage /> },
      { path: 'relations', element: <RelationsPage /> },
      { path: 'approvals', element: <ApprovalsPage /> },
      { path: 'analytics', element: <AnalyticsPage /> },
      { path: 'audit-logs', element: <AuditLogsPage /> },
      {
        path: '*',
        element: (
          <PagePlaceholder
            eyebrow="404"
            title="페이지를 찾을 수 없습니다"
            description="사이드바에서 이동할 화면을 다시 선택해 주세요."
          />
        ),
      },
    ],
  },
])
