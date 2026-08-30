import { createBrowserRouter, Navigate } from 'react-router-dom'
import Layout from './Layout.jsx'
import DashboardPage from '../features/detection/pages/DashboardPage.jsx'
import AlarmsPage from '../features/detection/pages/AlarmsPage.jsx'
import AgentRunPage from '../features/agent/pages/AgentRunPage.jsx'
import AnalyticsPage from '../features/analytics/pages/AnalyticsPage.jsx'
import AuditLogPage from '../features/analytics/pages/AuditLogPage.jsx'
import DocumentsPage from '../features/knowledge/pages/DocumentsPage.jsx'
import OntologyPage from '../features/knowledge/pages/OntologyPage.jsx'
import KnowledgePage from '../features/knowledge/pages/KnowledgePage.jsx'

// 라이트 테마 개편 — 네비 7개 (알람 히스토리 · 문서 검색 · 온톨로지 포함).
// 트레이스 뷰어(/traces) · 조치 목록(/actions)은 라우트를 제거해 URL 접근도 막는다 (팀 합의,
// 미지의 경로는 아래 캐치올이 /dashboard 로 돌린다). 페이지 파일은 남의 파트 코드라 삭제하지 않는다.
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
      { path: 'agent-runs', element: <AgentRunPage /> },
      { path: 'agent-runs/:runId', element: <AgentRunPage /> },
      { path: 'documents', element: <DocumentsPage /> },
      { path: 'ontology', element: <OntologyPage /> },
      { path: 'analytics', element: <AnalyticsPage /> },
      { path: 'audit-logs', element: <AuditLogPage /> },
      // B 화면 노출 여부 팀 결정 대기 — 사이드바 숨김, 라우트 유지
      { path: 'knowledge', element: <KnowledgePage /> },
      { path: '*', element: <Navigate to="/dashboard" replace /> },
    ],
  },
])
