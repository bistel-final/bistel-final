// 조치 종류별로 관여하는 n8n 워크플로와 팀 n8n의 실제 workflow ID(2026-09 기준 배포본).
export const WORKFLOW_IDS = Object.freeze({
  WF2: 'Icj3PPKG9F11Mupa',
  WF3: '9YxPhUXhGjTvV2Gt',
  WF4: 'ej11MKcoBdSchhY2',
})

export const workflowsForAction = (action) => {
  const code = action?.action_code
  if (code === 'EQP_HOLD') return ['WF2', 'WF3', 'WF4']
  if (code === 'WARNING') return ['WF2']
  return []
}
