import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Controls, Handle, MarkerType, Panel, Position, ReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { getAuditLogsCore } from '../../../shared/api/analytics.js'
import { fmtDateTime } from '../../../shared/api/format.js'
import { getChamberRelationsCore } from '../../../shared/api/knowledge.js'
import { auditTargetsOf, mergeAuditItems } from '../../../shared/components/audit/run-audit-view-state.js'
import { auditActorLabel, auditEntityLabel, auditEventLabel, auditValueLabel } from '../../../shared/components/audit/auditLabels.js'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'
import { STOP_LABELS, investigationTimelineState } from '../investigation-view.js'
import {
  layoutOntologyNodes,
  normalizeOntologyGraph,
  ONTOLOGY_RELATION_LABELS,
  ONTOLOGY_REVERSED_RELATION_LABELS,
  orientOntologyRelationships,
} from '../../../shared/graph/ontology-graph.js'
import { evidenceHref } from '../agent-run-view-state.js'
import DeliveryFlow from './DeliveryFlow.jsx'
import { deliveryFlowEdges, isNotificationAction } from '../notification-state.js'
import RunRagEvidenceTab from './RunRagEvidenceTab.jsx'
import {
  alarmDisplayLabel,
  approvalStatusSummary,
  approvalText,
  deliveryStatusSummary,
  diagnosticReasonText,
  diagnosticStatusText,
  impactLabelOf,
  impactSourceOf,
  supportingAlarmLabel,
  toolStatusText,
} from './agentModel.js'

const REACT_PHASE_LABELS = { SELECTED: '선택', OBSERVED: '관찰', REJECTED: '선택 거부', STOPPED: '종료' }

const STEP_META = Object.freeze({
  alarm: ['알람 Incident', '#6b849d', '입력'],
  fdc: ['측정 데이터 근거', '#6b849d', '근거 수집'],
  history: ['이력 · 대조 근거', '#6b849d', '근거 수집'],
  metrology: ['계측 결과 근거', '#6b849d', '근거 수집'],
  rag: ['매뉴얼 문서 근거', '#6b849d', '근거 수집'],
  graph: ['설비 관계 근거', '#6b849d', '근거 수집'],
  tools: ['분석 근거 조회', '#6b849d', '근거 수집'],
  assessment: ['근거 충분성 판단', '#846f9d', '조건 분기'],
  react: ['부족 근거 Tool 재선택', '#846f9d', '조사 루프'],
  diagnosis: ['Incident 종합 진단', '#47769d', '분석'],
  prediction: ['AI 원인 가설', '#47769d', '분석'],
  action: ['규칙 조치 판정', '#4f8172', '조치'],
  approval: ['사람 승인', '#4f8172', '조치'],
  delivery: ['전달', '#4f8172', '조치'],
  audit: ['감사 기록', '#6b7280', '기록'],
})

const evidenceBy = (detail, types) =>
  (detail.evidence_items ?? []).filter((item) => types.includes(item.type))

const succeededCalls = (detail, toolName) =>
  (detail.tools ?? []).filter((tool) => tool.tool_name === toolName && tool.status === 'SUCCESS')

// 인용된 근거가 있으면 그것을, 없으면 성공한 조회 기록을 단계 값으로 쓴다.
const collectedBy = (detail, types, toolName) => {
  const items = evidenceBy(detail, types)
  return items.length ? items : succeededCalls(detail, toolName)
}

const observedSteps = (detail, toolName) =>
  (detail.react_trace ?? []).filter((entry) => entry.tool === toolName && entry.phase === 'OBSERVED')

const executionStepsOf = (detail) => [
  { id: 'alarm', value: evidenceBy(detail, ['ALARM']) },
  { id: 'fdc', value: collectedBy(detail, ['TRACE'], 'get_fdc_summary') },
  { id: 'history', value: succeededCalls(detail, 'get_chamber_parameter_history') },
  { id: 'metrology', value: collectedBy(detail, ['METROLOGY'], 'get_metrology_result') },
  { id: 'rag', value: collectedBy(detail, ['DOCUMENT'], 'search_documents') },
  { id: 'graph', value: collectedBy(detail, ['GRAPH'], 'get_equipment_context') },
  { id: 'tools', value: detail.tools ?? [] },
  { id: 'assessment', value: detail.evidence_assessment },
  { id: 'react', value: detail.react_trace ?? null },
  { id: 'diagnosis', value: detail.diagnosis },
  { id: 'prediction', value: detail.prediction },
  { id: 'action', value: detail.action },
  { id: 'approval', value: detail.approval },
  { id: 'delivery', value: detail.action?.deliveries ?? [] },
  { id: 'audit', value: { run_id: detail.agent_run_id } },
].filter((step) => step.id !== 'approval' || !isNotificationAction(detail.action))

const isAvailable = (step) =>
  Array.isArray(step.value) ? step.value.length > 0 : step.value != null

const nodeLabel = (step, selected, incidentScopeLabel = null, current = false) => {
  const [label, color, phase] = STEP_META[step.id]
  return (
    <div className="text-center">
      <div className="text-[11.5px] font-extrabold tracking-[.08em]" style={{ color }}>{phase}</div>
      <div className="mt-1 text-[15px] font-extrabold text-ink">{label}</div>
      {step.id === 'alarm' && incidentScopeLabel && (
        <div className="mt-1 whitespace-nowrap font-mono text-[10.5px] font-bold text-g1">({incidentScopeLabel} 기준)</div>
      )}
      <div className={`mt-1 text-[11.5px] font-semibold ${current ? 'text-blue' : 'text-g2'}`}>
        {current ? '수행 중…' : isAvailable(step) ? (selected ? '선택됨' : step.id === 'delivery' ? '전달 상태 조회' : '완료') : '미수행'}
      </div>
    </div>
  )
}

// 시연 가독성: 단계 간 세로 간격을 넓히고 좌우 가지를 벌려 노드·라벨이 겹치지 않게 한다.
// 왼쪽 열 = 입력·근거 수집·근거 충분성 판단, 오른쪽 열 = 진단·조치·전달·기록. 두 열로 나눠 화면을 구분한다.
const FLOW_LAYOUT = Object.freeze({
  alarm: { x: 330, y: 0 },
  tools: { x: 330, y: 150 },
  fdc: { x: -290, y: 310 },
  history: { x: 20, y: 310 },
  metrology: { x: 330, y: 310 },
  rag: { x: 640, y: 310 },
  graph: { x: 950, y: 310 },
  // 마름모(122px)와 일반 노드(220px)의 중심을 맞추기 위해 결정 노드 x는 +49.
  assessment: { x: 379, y: 480, decision: true },
  react: { x: 10, y: 512 },
  // 오른쭉 열은 근거 충분성 판단과 같은 높이에서 시작해 화살표가 수평으로 건너간다.
  diagnosis: { x: 1180, y: 495, leftEntry: true },
  prediction: { x: 1180, y: 645 },
  action: { x: 1229, y: 795, decision: true },
  approval: { x: 940, y: 965 },
  delivery: { x: 1440, y: 965 },
  audit: { x: 1180, y: 1125 },
})

const edgeStyle = Object.freeze({ stroke: '#8095a9', strokeWidth: 1.65 })
const experimentEdgeStyle = Object.freeze({ stroke: '#9b8bab', strokeWidth: 1.55, strokeDasharray: '6 5' })

const flowEdge = (id, source, target, options = {}) => ({
  id,
  source,
  target,
  type: 'smoothstep',
  markerEnd: { type: MarkerType.ArrowClosed, color: options.experimental ? '#9b8bab' : '#8095a9' },
  style: options.experimental ? experimentEdgeStyle : edgeStyle,
  label: options.label,
  labelStyle: { fill: options.experimental ? '#776783' : '#64788b', fontSize: 12.5, fontWeight: 700 },
  labelBgStyle: { fill: '#f8fafc', fillOpacity: 0.94 },
  sourceHandle: options.sourceHandle,
  targetHandle: options.targetHandle,
})

const FLOW_EDGES = Object.freeze([
  flowEdge('alarm-tools', 'alarm', 'tools', { targetHandle: 'top' }),
  flowEdge('tools-fdc', 'tools', 'fdc'),
  flowEdge('tools-history', 'tools', 'history'),
  flowEdge('tools-metrology', 'tools', 'metrology'),
  flowEdge('tools-rag', 'tools', 'rag'),
  flowEdge('tools-graph', 'tools', 'graph'),
  flowEdge('fdc-assessment', 'fdc', 'assessment'),
  flowEdge('history-assessment', 'history', 'assessment'),
  flowEdge('metrology-assessment', 'metrology', 'assessment'),
  flowEdge('rag-assessment', 'rag', 'assessment'),
  flowEdge('graph-assessment', 'graph', 'assessment'),
  flowEdge('assessment-diagnosis', 'assessment', 'diagnosis', { label: '근거 충분 · 현재 진행', sourceHandle: 'right', targetHandle: 'left' }),
  flowEdge('assessment-react', 'assessment', 'react', { label: '근거 일부 부족 · 추가 수집', sourceHandle: 'left' }),
  flowEdge('react-tools', 'react', 'tools', { label: '필요 Tool 선택', targetHandle: 'left' }),
  flowEdge('diagnosis-prediction', 'diagnosis', 'prediction'),
  flowEdge('prediction-action', 'prediction', 'action'),
  flowEdge('action-approval', 'action', 'approval', { label: 'EQP_HOLD', sourceHandle: 'left', targetHandle: 'top' }),
  flowEdge('action-delivery', 'action', 'delivery', { label: 'WARNING · MONITORING', sourceHandle: 'right', targetHandle: 'top' }),
  flowEdge('approval-delivery', 'approval', 'delivery', { label: '승인 후 전달', sourceHandle: 'right', targetHandle: 'left' }),
  flowEdge('delivery-audit', 'delivery', 'audit', { sourceHandle: 'bottom' }),
])

function DecisionNode({ data, selected }) {
  return (
    <div className="relative flex h-[122px] w-[122px] items-center justify-center">
      <Handle id="top" type="target" position={Position.Top} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      <Handle id="bottom" type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      <Handle id="left" type="source" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      <Handle id="right" type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      <div
        className="absolute inset-[18px] rotate-45 rounded-[12px] bg-white"
        style={{ border: `${selected ? 2.5 : 1.25}px solid ${selected ? data.color : '#bdc9d4'}`, boxShadow: selected ? `0 0 0 4px ${data.color}12` : '0 2px 7px rgba(15,23,42,.05)' }}
      />
      <div className="relative z-10 w-[108px]">{data.label}</div>
    </div>
  )
}

// 실행 중인 단계는 선택 여부와 무관하게 노드 자체를 그 단계 색으로 강조한다.
const emphasisStyle = (data, selected) => ({
  border: `${selected || data.current ? 2.5 : 1.25}px solid ${selected || data.current ? data.color : '#cad5df'}`,
  background: data.current ? `${data.color}1f` : selected ? `${data.color}12` : '#fff',
  boxShadow: data.current
    ? `0 0 0 5px ${data.color}22`
    : selected ? `0 0 0 4px ${data.color}12` : '0 2px 7px rgba(15,23,42,.05)',
})

function ToolPlanNode({ data, selected }) {
  return (
    <div
      className="relative flex min-h-[92px] w-[220px] items-center justify-center rounded-[10px] px-3 py-2.5"
      style={emphasisStyle(data, selected)}
    >
      <Handle id="top" type="target" position={Position.Top} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      <Handle id="left" type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      <Handle id="bottom" type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      {data.label}
    </div>
  )
}

function ApprovalStepNode({ data, selected }) {
  return (
    <div className="relative flex min-h-[92px] w-[220px] items-center justify-center rounded-[10px] px-3 py-2.5" style={emphasisStyle(data, selected)}>
      <Handle id="top" type="target" position={Position.Top} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      <Handle id="right" type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      {data.label}
    </div>
  )
}

function DeliveryStepNode({ data, selected }) {
  return (
    <div className="relative flex min-h-[92px] w-[220px] items-center justify-center rounded-[10px] px-3 py-2.5" style={emphasisStyle(data, selected)}>
      <Handle id="top" type="target" position={Position.Top} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      <Handle id="left" type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      <Handle id="bottom" type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !border-0 !bg-slate-500" />
      {data.label}
    </div>
  )
}

const NODE_TYPES = Object.freeze({
  decision: DecisionNode,
  toolPlan: ToolPlanNode,
  approvalStep: ApprovalStepNode,
  deliveryStep: DeliveryStepNode,
})

const TOOL_LABELS = Object.freeze({
  get_fdc_summary: 'FDC 요약 조회',
  get_chamber_parameter_history: '챔버 이력 · 대조 조회',
  get_metrology_result: '계측 결과 조회',
  get_equipment_context: '장비 · 공정 관계 조회',
  search_documents: '매뉴얼 문서 검색',
  send_action: '조치 전달',
})

const EVIDENCE_TOOL = Object.freeze({
  fdc: 'get_fdc_summary',
  history: 'get_chamber_parameter_history',
  metrology: 'get_metrology_result',
  rag: 'search_documents',
  graph: 'get_equipment_context',
})

const TOOL_GUIDES = Object.freeze({
  get_fdc_summary: {
    purpose: '알람이 난 LOT의 측정 추세와 관리 한계 초과 여부를 확인했습니다.',
    success: '측정 데이터와 FDC 판정 근거를 확보했습니다.',
    failure: '측정 데이터 근거를 확보하지 못했습니다.',
  },
  get_chamber_parameter_history: {
    purpose: '같은 파라미터의 이전 LOT 추세와 형제 챔버 값을 대조했습니다.',
    success: '이력·대조 근거를 확보했습니다.',
    failure: '이력·대조 근거를 확보하지 못했습니다.',
  },
  get_metrology_result: {
    purpose: '해당 공정 경로의 품질 계측값과 판정 결과를 확인했습니다.',
    success: '계측 결과 근거를 확보했습니다.',
    failure: '계측 결과 근거를 확보하지 못했습니다.',
  },
  get_equipment_context: {
    purpose: '해당 챔버의 설비·공정·파라미터 연결 관계와 영향 범위를 확인했습니다.',
    success: '설비와 공정의 연결 관계 근거를 확보했습니다.',
    failure: '설비 관계 근거를 확보하지 못했습니다.',
  },
  search_documents: {
    purpose: '매뉴얼과 SOP에서 가능한 원인 및 권고 조치 근거를 검색했습니다.',
    success: '관련 매뉴얼 문서 근거를 확보했습니다.',
    failure: '관련 매뉴얼 문서 근거를 확보하지 못했습니다.',
  },
  send_action: {
    purpose: '확정된 조치를 담당 채널로 전달하고 결과를 확인했습니다.',
    success: '조치 전달 결과를 확인했습니다.',
    failure: '조치를 전달하지 못했습니다.',
  },
})

const EVIDENCE_GUIDES = Object.freeze({
  ALARM: ['기준 알람', '분석을 시작한 알람의 발생 대상과 시각을 확인했습니다.'],
  TRACE: ['측정 추세', '실측값과 관리 한계를 비교해 이상 흐름을 확인했습니다.'],
  METROLOGY: ['계측 결과', '웨이퍼 계측값에서 공정 이상 신호를 확인했습니다.'],
  DOCUMENT: ['매뉴얼 근거', '관련 문서에서 원인 후보와 권고 조치를 확인했습니다.'],
  GRAPH: ['설비 관계', '장비·챔버·파라미터의 연결 관계와 영향 대상을 확인했습니다.'],
})

function GraphEvidenceList({ detail, items }) {
  const [relations, setRelations] = useState({
    chamberId: detail.chamber_id,
    phase: 'loading',
    values: new Map(),
  })

  useEffect(() => {
    let active = true
    getChamberRelationsCore(detail.chamber_id).then(
      (response) => {
        if (!active) return
        const graph = normalizeOntologyGraph(response)
        const nodeById = new Map((graph?.nodes ?? []).map((node) => [node.id, node]))
        const layout = layoutOntologyNodes(graph)
        const values = new Map(
          orientOntologyRelationships(graph, layout).map((relationship) => {
            const source = nodeById.get(relationship.display_source)
            const target = nodeById.get(relationship.display_target)
            const labelMap = relationship.display_reversed
              ? ONTOLOGY_REVERSED_RELATION_LABELS
              : ONTOLOGY_RELATION_LABELS
            return [relationship.id, {
              source: source?.display_name ?? source?.business_id,
              label: labelMap[relationship.type] ?? relationship.type,
              target: target?.display_name ?? target?.business_id,
            }]
          }),
        )
        setRelations({ chamberId: detail.chamber_id, phase: 'success', values })
      },
      () => {
        if (active) setRelations({ chamberId: detail.chamber_id, phase: 'error', values: new Map() })
      },
    )
    return () => { active = false }
  }, [detail.chamber_id])

  const currentRelations = relations.chamberId === detail.chamber_id
    ? relations
    : { phase: 'loading', values: new Map() }
  const relationOf = (item) => {
    const relationId = String(item.source_id ?? '').match(/(?:relation=)?(REL-[^;\s]+)/)?.[1]
    return relationId ? currentRelations.values.get(relationId) : null
  }

  return (
    <div className="rounded-lg border border-line bg-soft px-3 py-3">
      <div className="flex items-center justify-between gap-3">
        <strong className="text-[12px] text-navy">설비 관계 근거</strong>
        <Badge variant="t-blue">연결 {items.length}건 확인</Badge>
      </div>
      <div className="mt-2 text-[11.5px] font-semibold leading-5 text-g1">
        이 챔버에서 설비·공정 단계·파라미터로 이어지는 관계를 확인해 영향 범위 판단에 사용했습니다.
      </div>
      <div className="mt-3 space-y-2">
        {items.map((item, index) => {
          const relation = relationOf(item)
          return (
            <div key={`relation-summary:${item.source_id}`} className="rounded-md border border-cell-line bg-white px-3 py-2.5">
              <div className="text-[10px] font-extrabold text-g2">확인 관계 {index + 1}</div>
              {relation ? (
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-ink">
                  <strong className="text-navy">{relation.source}</strong>
                  <span className="font-bold text-blue">— {relation.label} →</span>
                  <strong className="text-navy">{relation.target}</strong>
                </div>
              ) : (
                <div className="mt-1 text-[11.5px] text-g1">
                  {currentRelations.phase === 'loading'
                    ? '연결된 노드를 확인하고 있습니다.'
                    : '연결 노드 정보는 아래 직접 보기에서 확인할 수 있습니다.'}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {items.map((item, index) => {
          const href = evidenceHref(item, { chamberId: detail.chamber_id })
          if (!href) return null
          return (
            <a
              key={item.source_id}
              href={href}
              title={item.source_id}
              className="rounded-md border border-tint-blue-line bg-white px-3 py-2 text-[11px] font-extrabold text-blue hover:bg-tint-blue"
            >
              연결 {index + 1} 직접 보기 →
            </a>
          )
        })}
      </div>
    </div>
  )
}

function EvidenceList({ detail, items }) {
  if (!items?.length) return <div className="text-[12px] text-g2">근거 없음</div>
  if (items.every((item) => item.type === 'DOCUMENT')) {
    const hits = items.map((item) => ({
      source_id: item.source_id,
      title: item.title,
      document_id: item.document_id,
      chunk_id: item.chunk_id,
      section: item.section,
      content: item.excerpt,
      score: null,
      href: evidenceHref(item),
    }))
    return <RunRagEvidenceTab hits={hits} diagnosis={detail.diagnosis} compact />
  }
  if (items.every((item) => item.type === 'GRAPH')) {
    return <GraphEvidenceList detail={detail} items={items} />
  }
  return (
    <div className="flex flex-col gap-2">
      {items.map((item, index) => {
        const href = evidenceHref(item, { chamberId: detail.chamber_id })
        const [label, guide] = EVIDENCE_GUIDES[item.type] ?? ['분석 근거', '에이전트가 확인한 근거입니다.']
        const graphEvidence = item.type === 'GRAPH'
        const excerpt = graphEvidence
          ? '현재 챔버에서 설비·공정 단계·파라미터로 이어지는 연결을 분석 근거로 사용했습니다.'
          : item.excerpt
        const metadata = [
          item.section ? `문서 위치 ${item.section}` : null,
          item.alarm?.source ? `알람 종류 ${item.alarm.source}` : null,
        ].filter(Boolean)
        const body = (
          <div className="rounded-lg border border-line bg-soft px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <strong className="text-[12px] text-navy">{label}</strong>
              <span className="text-[10px] font-bold text-g2" title={item.source_id}>사용 근거 {index + 1}</span>
            </div>
            <div className="mt-1 text-[11px] font-semibold text-g1">{guide}</div>
            <div className="mt-2 rounded-md border border-cell-line bg-white px-2.5 py-2 text-[11.5px] leading-5 text-ink">{excerpt}</div>
            {metadata.length > 0 && <div className="mt-1.5 text-[10px] text-g2">{metadata.join(' · ')}</div>}
            {href && <div className="mt-2 text-[11px] font-extrabold text-blue">{graphEvidence ? '연결 관계 직접 보기' : '근거 화면에서 확인'} →</div>}
          </div>
        )
        return href ? <a key={item.source_id} href={href}>{body}</a> : <div key={item.source_id}>{body}</div>
      })}
    </div>
  )
}

function PredictionPanel({ detail }) {
  const prediction = detail.prediction
  if (!prediction) return <div className="text-[12px] font-semibold text-g2">AI 판단 미완료</div>
  const citations = [
    ...prediction.supporting_alarms.map((item) => ({
      key: `${item.source}:${item.alarm_id}`,
      label: supportingAlarmLabel(item),
    })),
    ...prediction.supporting_chunk_ids.map((id) => ({ key: id, label: id })),
    ...prediction.supporting_relation_ids.map((id) => ({ key: id, label: id })),
  ]
  const evidence = new Map((detail.evidence_items ?? []).map((item) => [item.source_id, item]))
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="t-blue">{prediction.predicted_fault_code}</Badge>
        <strong className="font-mono text-[12px]">신뢰도 {Math.round(prediction.confidence * 100)}%</strong>
      </div>
      <div className="rounded-lg border border-tint-blue-line bg-tint-blue px-3 py-2.5 text-[12px] text-ink">{prediction.cause_summary}</div>
      <div>
        <div className="mb-1 text-[10.5px] font-bold text-g2">검증된 인용 근거</div>
        <div className="flex flex-wrap gap-1.5">
          {citations.map(({ key, label }) => {
            const item = evidence.get(key)
            const href = item ? evidenceHref(item, { chamberId: detail.chamber_id }) : null
            const chip = <Badge variant={item ? 't-green' : 't-gray'}>{item ? label : `${label} · 근거 연결 불가`}</Badge>
            return href ? <a key={key} href={href}>{chip}</a> : <span key={key}>{chip}</span>
          })}
          {citations.length === 0 && <span className="text-[11px] text-g2">인용 근거 없음</span>}
        </div>
      </div>
      <div className="text-[11.5px] text-g1">불확실성: {prediction.uncertainty || '명시 없음'}</div>
      <div className="grid grid-cols-2 gap-2 font-mono text-[10.5px] text-g2">
        <span>model {prediction.llm_model}</span><span>prompt {prediction.prompt_version}</span>
        <span>input {prediction.input_tokens.toLocaleString()} tokens</span><span>output {prediction.output_tokens.toLocaleString()} tokens</span>
        <span>생성 {fmtDateTime(prediction.generated_at)}</span><span>latency {detail.latency_ms.toLocaleString()}ms</span>
      </div>
    </div>
  )
}

const EmptyBlock = ({ text }) => (
  <div className="rounded-lg border border-dashed border-field-line bg-soft px-3 py-2 text-[11.5px] text-g2">
    {text}
  </div>
)

function PreviewGrid({ items }) {
  const visible = items.filter(([, value]) => value != null && value !== '')
  if (visible.length === 0) return <EmptyBlock text="표시할 실측 정보가 없습니다." />
  return (
    <div className="grid grid-cols-2 gap-2">
      {visible.map(([label, value]) => (
        <div key={label} className="min-w-0 rounded-lg border border-line bg-soft px-3 py-2.5">
          <div className="text-[10px] font-bold text-g2">{label}</div>
          <div className="mt-1 break-words font-mono text-[12px] font-semibold text-ink">{String(value)}</div>
        </div>
      ))}
    </div>
  )
}

function AlarmPreview({ detail, alarm, items }) {
  const evidenceAlarm = items?.[0]?.alarm
  const source = alarm?.source ?? evidenceAlarm?.source ?? detail.alarm_source
  const alarmId = alarm?.alarm_id ?? evidenceAlarm?.alarm_id ?? detail.alarm_id
  const alarmLabel = alarmDisplayLabel({
    source,
    alarmId,
    chamberId: alarm?.chamber_id ?? detail.chamber_id,
    lotId: alarm?.lot_id ?? detail.lot_id,
    waferNo: alarm?.wafer_no,
  })
  const wafer = alarm?.wafer_id ?? (alarm?.wafer_no != null ? `W${alarm.wafer_no}` : null)
  const step = [alarm?.recipe_step_name, alarm?.recipe_step_no].filter((value) => value != null && value !== '').join(' · ')
  return (
    <div className="space-y-3">
      <PreviewGrid items={[
        ['분석 대상', alarmLabel],
        ['발생 시각', alarm?.occurred_at ? fmtDateTime(alarm.occurred_at) : null],
        ['LOT · WAFER', [alarm?.lot_id, wafer].filter(Boolean).join(' · ')],
        ['설비 · 챔버', [alarm?.equipment_id, alarm?.chamber_id ?? detail.chamber_id].filter(Boolean).join(' · ')],
        ['파라미터', alarm?.parameter_id ?? alarm?.sensor_id],
        ['Recipe Step', step],
        ['판정 · 규칙', [alarm?.alarm_type ?? alarm?.judgement, alarm?.rule_code ?? alarm?.rule_id].filter(Boolean).join(' · ')],
        ['측정값', alarm?.value],
      ]} />
      {alarm?.detail && (
        <div className="rounded-lg border border-line bg-white px-3 py-2.5 text-[12px] leading-6 text-g1">
          <strong className="text-navy">알람 상세:</strong> {typeof alarm.detail === 'string' ? alarm.detail : JSON.stringify(alarm.detail)}
        </div>
      )}
      <EvidenceList detail={detail} items={items} />
    </div>
  )
}

const auditSummaryOf = (item) => {
  const after = item.after && typeof item.after === 'object' ? item.after : null
  if (!after) return null
  return [after.status, after.channel, after.predicted_fault_code]
    .filter(Boolean)
    .map((value) => auditValueLabel(value))
    .join(' · ') || null
}

function AuditPreview({ detail }) {
  const [state, setState] = useState({ phase: 'loading', items: [], error: null })
  const actionId = detail.action?.action_id
  const approvalId = detail.approval?.approval_id

  useEffect(() => {
    let active = true
    const targets = auditTargetsOf({
      agent_run_id: detail.agent_run_id,
      action_id: actionId,
      approval_id: approvalId,
    })
    Promise.all(targets.map(([entity_type, entity_id]) => getAuditLogsCore({ entity_type, entity_id })))
      .then((groups) => {
        if (active) setState({ phase: 'success', items: mergeAuditItems(groups), error: null })
      })
      .catch((error) => {
        if (active) setState({ phase: 'error', items: [], error: error?.message ?? '감사기록 조회 실패' })
      })
    return () => { active = false }
  }, [actionId, approvalId, detail.agent_run_id])

  if (state.phase === 'loading') return <EmptyBlock text="이 실행의 감사기록을 불러오는 중입니다." />
  if (state.phase === 'error') return <EmptyBlock text={state.error} />
  return (
    <div className="space-y-3">
      <PreviewGrid items={[
        ['Agent 실행', detail.agent_run_id],
        ['조치', actionId],
        ['승인', approvalId],
        ['기록 수', `${state.items.length}건`],
      ]} />
      {state.items.length > 0 ? (
        <div className="divide-y divide-cell-line overflow-hidden rounded-lg border border-line bg-white">
          {state.items.slice(0, 8).map((item) => (
            <div key={item.audit_id} className="px-3 py-2.5">
              <div className="flex items-center justify-between gap-3">
                <strong className="text-[12px] text-navy" title={item.event_type}>{auditEventLabel(item.event_type)}</strong>
                <span className="shrink-0 font-mono text-[10.5px] text-g2">{fmtDateTime(item.occurred_at)}</span>
              </div>
              <div className="mt-1 text-[11px] text-g1">
                {auditEntityLabel(item.entity_type)}: <span className="font-mono">{item.entity_id}</span>
                {' · '}{auditActorLabel(item.actor_type)}{auditSummaryOf(item) ? ` · ${auditSummaryOf(item)}` : ''}
              </div>
            </div>
          ))}
        </div>
      ) : <EmptyBlock text="이 실행에 연결된 감사기록이 없습니다." />}
      <Link to={`/audit-logs?entity_id=${encodeURIComponent(detail.agent_run_id)}`} className="inline-flex text-[11.5px] font-bold text-blue">
        전체 감사로그에서 보기 →
      </Link>
    </div>
  )
}

function ReactLoopPanel({ detail }) {
  const view = investigationTimelineState(detail)
  const budget = typeof detail.remaining_read_calls === 'number'
    ? `남은 조회 예산 ${detail.remaining_read_calls} / ${detail.investigation_budget?.read_cap ?? 8}`
    : null
  if (view.phase !== 'success') {
    return (
      <div className="space-y-3 text-[12px] text-g1">
        <div className="rounded-lg border border-line bg-soft px-3 py-2.5">
          확보된 근거와 누락 source를 보고 FDC·RAG·Graph 중 다음 조회 Tool을 스스로 고릅니다. 선택은 코드 가드가 검증합니다.
        </div>
        <EmptyBlock text={view.message ?? '조사 루프 기록 없음'} />
      </div>
    )
  }
  return (
    <div className="space-y-3 text-[12px] text-g1">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Badge variant="t-blue">조사 {detail.react_trace.length}단계</Badge>
        {budget && <span className="text-[11px] text-g2">{budget}</span>}
      </div>
      <ol className="space-y-2">
        {detail.react_trace.map((step) => (
          <li key={step.seq} className="rounded-lg border border-line bg-white px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-2 text-[12px] font-bold text-navy">
              <span className="font-mono text-g2">{step.seq}</span>
              <span>{REACT_PHASE_LABELS[step.phase] ?? step.phase}</span>
              <span>{TOOL_LABELS[step.tool] ?? '시스템'}</span>
              {step.degraded && <span className="text-tint-amber-text">수집된 근거로 가설 생성</span>}
            </div>
            {step.rationale_summary && <p className="mt-1.5 leading-6 text-ink">{step.rationale_summary}</p>}
            {step.argument_summary && <p className="mt-1 text-[11px] text-g2">대상: {step.argument_summary}</p>}
            {step.observation_summary && <p className="mt-1.5 leading-6 text-ink">관찰: {step.observation_summary}</p>}
            {step.guard_code && <p className="mt-1.5 text-[11px] text-tint-amber-text">선택 검증: {step.guard_code}</p>}
            {step.stop_reason && <p className="mt-1.5 text-[11px] text-g2">{STOP_LABELS[step.stop_reason] ?? '조사 종료'}</p>}
          </li>
        ))}
      </ol>
    </div>
  )
}

function DiagnosisPanel({ detail }) {
  const diagnosis = detail.diagnosis
  const assessment = detail.evidence_assessment
  const impact = detail.impact_scope
  const similar = detail.similar_incidents
  const postAction = detail.post_action_observation
  return (
    <div className="space-y-3 text-[11.5px] text-g1" data-testid="agent-diagnosis-five-blocks">
      <section>
        <div className="mb-1 flex items-center justify-between">
          <strong className="text-navy">1. 종합 진단</strong>
          <Badge variant={diagnosis?.status === 'AVAILABLE' ? 't-blue' : 't-gray'}>{diagnosticStatusText(diagnosis?.status)}</Badge>
        </div>
        {diagnosis?.status === 'AVAILABLE' ? (
          <div className="space-y-2 rounded-lg border border-tint-blue-line bg-tint-blue px-3 py-2.5">
            <div className="font-semibold text-ink">{diagnosis.cause_summary}</div>
            <div>{diagnosis.diagnostic_coverage}</div>
            <ul className="list-disc space-y-1 pl-4">{diagnosis.observations.map((item) => <li key={item}>{item}</li>)}</ul>
            <div><strong>근거 종합:</strong> {diagnosis.evidence_synthesis || '명시 없음'}</div>
            {diagnosis.alternative_hypotheses.map((item) => (
              <div key={`${item.summary}:${item.lower_rank_reason}`}><strong>대안:</strong> {item.summary} — {item.lower_rank_reason}</div>
            ))}
            <div><strong>다음 확인:</strong> {diagnosis.verification_steps.join(' → ') || '명시 없음'}</div>
            <div><strong>한계:</strong> {diagnosis.limitations.join(' · ') || '명시 없음'}</div>
          </div>
        ) : <EmptyBlock text={`종합 진단 없음 · ${diagnosticReasonText(diagnosis?.reason_code)}`} />}
      </section>

      <section>
        <div className="mb-1 flex items-center justify-between"><strong className="text-navy">2. 근거 충분성</strong><Badge variant={assessment?.status === 'SUFFICIENT' ? 't-green' : assessment?.status === 'CONFLICT' ? 't-red' : 't-amber'}>{diagnosticStatusText(assessment?.status)}</Badge></div>
        <div className="rounded-lg border border-line bg-soft px-3 py-2">
          확인 {assessment?.available_sources?.join(' · ') || '없음'} / 누락 {assessment?.missing_sources?.join(' · ') || '없음'}
          {assessment?.reason_codes?.length > 0 && <div className="mt-1 font-mono text-[10px] text-g2">{assessment.reason_codes.join(' · ')}</div>}
          {assessment?.conflicting_source_ids?.length > 0 && <div className="mt-1 text-red">충돌 ID {assessment.conflicting_source_ids.join(' · ')}</div>}
        </div>
      </section>

      <section>
        <div className="mb-1 flex items-center justify-between"><strong className="text-navy">3. 영향 범위</strong><Badge variant={impact?.status === 'AVAILABLE' ? 't-blue' : 't-gray'}>{diagnosticStatusText(impact?.status)}</Badge></div>
        {impact?.status === 'AVAILABLE' ? (
          <div className="space-y-2 rounded-lg border border-line bg-soft px-3 py-2.5">
            {impact.summary && <div className="font-semibold leading-5 text-ink">{impact.summary}</div>}
            <div>
              <strong>직접 영향 대상:</strong>{' '}
              {impact.direct.map((item) => `${impactLabelOf(item)} ${impactSourceOf(item)}`).join(' · ') || '없음'}
            </div>
            <div>
              <strong>추가 확인 대상:</strong>{' '}
              {impact.check_required.map((item) => `${impactLabelOf(item)} ${impactSourceOf(item)}`).join(' · ') || '없음'}
            </div>
            {impact.graph_conflict && <div className="mt-1 font-bold text-red">Graph 충돌 — PostgreSQL 실제 route 우선</div>}
          </div>
        ) : <EmptyBlock text={`영향 범위 없음 · ${diagnosticReasonText(impact?.reason_code)}`} />}
      </section>

      <section>
        <div className="mb-1 flex items-center justify-between"><strong className="text-navy">4. 유사 incident</strong><Badge variant={similar?.status === 'AVAILABLE' ? 't-green' : 't-gray'}>{diagnosticStatusText(similar?.status)}</Badge></div>
        {similar?.items?.length ? (
          <div className="space-y-1 rounded-lg border border-line bg-soft px-3 py-2">
            <div className="text-[10px] text-g2">{similar.label}</div>
            {similar.items.map((item) => <div key={item.agent_run_id}>{item.lot_id} · {item.chamber_id} · 유사도 {item.score}점</div>)}
          </div>
        ) : <EmptyBlock text={`유사 이력 없음 · ${diagnosticReasonText(similar?.reason_code ?? 'NOT_ENOUGH_RUNTIME_HISTORY')}`} />}
      </section>

      <section>
        <div className="mb-1 flex items-center justify-between"><strong className="text-navy">5. 조치 후 관찰</strong><Badge variant="t-gray">{diagnosticStatusText(postAction?.status)}</Badge></div>
        <EmptyBlock text={postAction?.message ?? '조치 후 관찰 정보 없음'} />
      </section>
    </div>
  )
}

function StepPanel({ detail, step, alarm }) {
  const title = STEP_META[step.id][0]
  let content = null
  if (step.id === 'alarm') content = <AlarmPreview detail={detail} alarm={alarm} items={step.value} />
  if (['fdc', 'history', 'metrology', 'rag', 'graph'].includes(step.id)) {
    // 인용된 근거가 있으면 그대로 보여 주고, 조회만 하고 인용되지 않았으면
    // 조사 기록에서 무엇을 대상으로 무엇을 봤는지 읽어 온다.
    const cited = step.value.filter((item) => item.type)
    const observed = observedSteps(detail, EVIDENCE_TOOL[step.id])
    content = cited.length ? <EvidenceList detail={detail} items={cited} /> : observed.length ? (
      <div className="space-y-2">
        {observed.map((entry) => (
          <div key={`${step.id}:${entry.seq}`} className="rounded-lg border border-line bg-soft px-3 py-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong className="text-[12px] text-navy">{entry.argument_summary ?? TOOL_LABELS[EVIDENCE_TOOL[step.id]]}</strong>
              <Badge variant="t-green">수집 완료</Badge>
            </div>
            {entry.rationale_summary && (
              <div className="mt-2 text-[11.5px] leading-5 text-g1"><strong className="text-navy">선택 이유:</strong> {entry.rationale_summary}</div>
            )}
            {entry.observation_summary && (
              <div className="mt-1 text-[11.5px] leading-5 text-g1"><strong className="text-navy">관찰:</strong> {entry.observation_summary}</div>
            )}
          </div>
        ))}
      </div>
    ) : step.value.length ? (
      <div className="rounded-lg border border-line bg-soft px-3 py-2.5 text-[12px] text-g1">
        {TOOL_LABELS[EVIDENCE_TOOL[step.id]]}를 {step.value.length}회 수행했습니다. 가설이 인용한 근거로는 채택되지 않았습니다.
      </div>
    ) : <EmptyBlock text="이 단계의 조회를 수행하지 않았습니다" />
  }

  if (step.id === 'tools') content = step.value.length ? (
    <div className="space-y-2">
      {step.value.map((tool, index) => {
        const guide = TOOL_GUIDES[tool.tool_name]
        const succeeded = tool.status === 'SUCCESS'
        return (
          <div key={`${tool.tool_name}:${index}`} className="rounded-lg border border-line bg-soft px-3 py-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong className="text-[12px] text-navy">{TOOL_LABELS[tool.tool_name] ?? '분석 근거 조회'}</strong>
              <Badge variant={succeeded ? 't-green' : 't-red'}>{toolStatusText(tool.status)}</Badge>
            </div>
            <div className="mt-2 text-[11.5px] leading-5 text-g1">
              {guide?.purpose ?? '분석에 필요한 외부 근거를 확인했습니다.'}
            </div>
            <div className="mt-2 rounded-md border border-cell-line bg-white px-2.5 py-2 text-[11.5px] font-semibold text-ink">
              {succeeded ? guide?.success : guide?.failure}
            </div>
            <div className="mt-1.5 text-[10px] text-g2" data-result-summary={tool.result_summary}>
              처리 시간 · {tool.latency_ms == null ? '개별 latency 미제공' : `${tool.latency_ms.toLocaleString()}ms`}
            </div>
          </div>
        )
      })}
    </div>
  ) : <div className="text-[12px] text-g2">미수행</div>
  if (step.id === 'assessment') {
    const assessment = detail.evidence_assessment
    content = assessment ? (
      <div className="space-y-3 text-[12px] text-g1">
        <Badge variant={assessment.status === 'SUFFICIENT' ? 't-green' : assessment.status === 'CONFLICT' ? 't-red' : 't-amber'}>{diagnosticStatusText(assessment.status)}</Badge>
        <div className="rounded-lg border border-line bg-soft px-3 py-2.5">
          <div><strong>확보:</strong> {assessment.available_sources?.join(' · ') || '없음'}</div>
          <div className="mt-1"><strong>누락:</strong> {assessment.missing_sources?.join(' · ') || '없음'}</div>
          {assessment.reason_codes?.length > 0 && <div className="mt-2 font-mono text-[10.5px] text-g2">{assessment.reason_codes.join(' · ')}</div>}
        </div>
      </div>
    ) : <EmptyBlock text="근거 충분성 판정 없음" />
  }
  if (step.id === 'react') content = <ReactLoopPanel detail={detail} />
  if (step.id === 'diagnosis') content = <DiagnosisPanel detail={detail} />
  if (step.id === 'prediction') content = <PredictionPanel detail={detail} />
  if (step.id === 'action') content = step.value ? (
    <div className="space-y-3">
      <PreviewGrid items={[
        ['조치 ID', step.value.action_id],
        ['판정', step.value.action_code],
        [isNotificationAction(step.value) ? '전달 정책' : '이전 승인 정책', approvalStatusSummary(step.value, detail.approval)],
        ['전달', deliveryStatusSummary(step.value)],
      ]} />
      <div className="rounded-lg border border-tint-blue-line bg-tint-blue px-3 py-2.5 text-[12px] leading-6 text-g1">{step.value.reason}</div>
      <div className="text-[11px] font-bold text-g2">{step.value.delivery_policy ?? 'ACTION-POLICY-V1'} 규칙 판정</div>
    </div>
  ) : <div className="text-[12px] text-g2">조치 미결정</div>
  if (step.id === 'approval') content = step.value ? (
    <div className="space-y-3 text-[12px] text-g1">
      <PreviewGrid items={[
        ['승인 ID', step.value.approval_id],
        ['상태', approvalText(step.value.status).label],
        ['결정자', step.value.decided_by ?? '결정자 미정'],
        ['결정 시각', step.value.decided_at ? fmtDateTime(step.value.decided_at) : '미결정'],
      ]} />
      {step.value.decision_comment && <div>{step.value.decision_comment}</div>}
    </div>
  ) : <div className="text-[12px] text-g2">{approvalStatusSummary(detail.action, detail.approval)}</div>
  if (step.id === 'delivery') content = <DeliveryFlow action={detail.action} compact />
  if (step.id === 'audit') content = <AuditPreview key={detail.agent_run_id} detail={detail} />
  return (
    <Card className="agent-step-panel max-h-[calc(100vh-150px)] overflow-y-auto p-5">
      <div className="mb-4 pr-10 text-[18px] font-extrabold text-navy">{title}</div>
      {content}
    </Card>
  )
}

function AgentExecutionFlow({ detail, alarm }) {
  const steps = useMemo(() => executionStepsOf(detail), [detail])
  const edges = useMemo(() => deliveryFlowEdges(FLOW_EDGES, detail.action), [detail.action])
  const incidentScopeLabel = useMemo(
    () => [detail.chamber_id ?? alarm?.chamber_id, detail.lot_id ?? alarm?.lot_id].filter(Boolean).join(' · ') || null,
    [alarm?.chamber_id, alarm?.lot_id, detail.chamber_id, detail.lot_id],
  )
  const defaultId = useMemo(() => {
    if (detail.status === 'WAITING_APPROVAL') return 'approval'
    if (detail.status === 'COMPLETED' || detail.status === 'FAILED') return 'audit'
    if (detail.action?.deliveries?.length) return 'delivery'
    if (detail.action) return 'action'
    if (detail.prediction) return 'prediction'
    if (detail.diagnosis?.status === 'AVAILABLE') return 'diagnosis'
    if (detail.evidence_assessment) return 'assessment'
    if (detail.tools?.length) return 'tools'
    return 'alarm'
  }, [detail])
  // 실행 중에는 완료된 단계 수와 다음에 수행할 단계를 화면에 그대로 보여 준다.
  const progress = useMemo(() => {
    const running = detail.status === 'RUNNING'
    const done = steps.filter(isAvailable).length
    const currentId = running ? steps.find((step) => !isAvailable(step))?.id ?? null : null
    return { running, done, total: steps.length, currentId }
  }, [detail.status, steps])
  const [userSelectedId, setUserSelectedId] = useState(null)
  const [expanded, setExpanded] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const selectedId = userSelectedId ?? defaultId
  const nodes = useMemo(() => steps.map((step) => {
    const selected = step.id === selectedId
    const current = step.id === progress.currentId
    const [, color] = STEP_META[step.id]
    const layout = FLOW_LAYOUT[step.id]
    const decision = layout.decision
    // 진단 노드는 왼쪽 열(근거 충분성 판단)에서 들어오는 화살표를 받도록 왼쪽 입구가 있는 노드 형태를 쓴다.
    const toolPlan = step.id === 'tools' || layout.leftEntry === true
    const routedStep = step.id === 'approval' ? 'approvalStep' : step.id === 'delivery' ? 'deliveryStep' : null
    const emphasis = current ? color : null
    return {
      id: step.id,
      position: { x: layout.x, y: layout.y },
      data: { label: nodeLabel(step, selected, incidentScopeLabel, current), color, current, emphasis },
      type: decision ? 'decision' : toolPlan ? 'toolPlan' : routedStep ?? 'default',
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
      draggable: false,
      style: decision || toolPlan || routedStep ? undefined : { width: 220, minHeight: 92, borderRadius: 10, border: `${selected || current ? 2.5 : 1.25}px solid ${selected || current ? color : '#cad5df'}`, background: current ? `${color}1f` : selected ? `${color}12` : '#fff', boxShadow: current ? `0 0 0 5px ${color}22` : selected ? `0 0 0 4px ${color}12` : '0 2px 7px rgba(15,23,42,.05)' },
    }
  }), [incidentScopeLabel, progress.currentId, selectedId, steps])
  const selected = steps.find((step) => step.id === selectedId) ?? steps[0]

  useEffect(() => {
    if (!expanded) return undefined
    const closeOnEscape = (event) => {
      if (event.key !== 'Escape') return
      if (detailOpen) setDetailOpen(false)
      else setExpanded(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [detailOpen, expanded])

  const openFlow = () => {
    setDetailOpen(false)
    setExpanded(true)
  }

  const closeFlow = () => {
    setDetailOpen(false)
    setExpanded(false)
  }

  const selectNode = (nodeId) => {
    setDetailOpen((open) => nodeId === selectedId ? !open : true)
    setUserSelectedId(nodeId)
  }

  return (
    <div data-testid="agent-execution-flow">
      <button type="button" className="block w-full text-left" onClick={openFlow} data-testid="agent-execution-flow-launcher">
        <Card className="agent-main-readable group overflow-hidden transition hover:border-blue/40 hover:shadow-md">
          <div className="flex items-center justify-between gap-4 px-5 pb-3 pt-4">
            <span className="text-[15px] font-extrabold text-navy">Agent 실행 흐름</span>
            <span className="flex min-w-0 items-center gap-2.5">
              <span className="h-1.5 w-28 flex-none overflow-hidden rounded-full bg-cell-line" aria-hidden="true">
                <span
                  className={`block h-full rounded-full ${progress.running ? 'bg-blue' : 'bg-green'}`}
                  style={{ width: `${Math.round((progress.done / progress.total) * 100)}%` }}
                />
              </span>
              <span className="whitespace-nowrap font-mono text-[11.5px] font-bold text-g1">
                {progress.done} / {progress.total}
              </span>
              <span className="hidden truncate text-[11.5px] text-g1 sm:inline">
                {progress.running
                  ? `${STEP_META[progress.currentId]?.[0] ?? '다음 단계'} 수행 중`
                  : '클릭하여 전체 흐름과 단계별 근거 확인'}
              </span>
            </span>
          </div>
          <div className="border-t border-cell-line px-5 py-4">
            <div className="grid grid-cols-4 items-center gap-2">
              {[
                ['입력', incidentScopeLabel ? `알람 Incident (${incidentScopeLabel} 기준)` : '알람 Incident'],
                ['근거 수집', '측정값 · 매뉴얼 · 설비 관계'],
                ['분석', '충분성 · 원인 · 영향'],
                ['조치', isNotificationAction(detail.action) ? '규칙 판정 · 자동 알림 · 모의 연동' : '규칙 판정 · 전달'],
              ].map(([phase, label], index) => (
                <div key={phase} className="relative rounded-lg border border-line bg-soft px-3 py-2.5">
                  <div className="text-[9px] font-extrabold tracking-[.08em] text-g2">{phase}</div>
                  <div className="mt-1 text-[11.5px] font-extrabold text-navy">{label}</div>
                  {index < 3 && <span className="absolute -right-3 top-1/2 z-10 -translate-y-1/2 text-[14px] font-bold text-blue">→</span>}
                </div>
              ))}
            </div>
            <div className="mt-3 text-right text-[11.5px] font-bold text-blue group-hover:text-blue-hover">
              실행 흐름 보기 →
            </div>
          </div>
        </Card>
      </button>

      {expanded && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-6" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeFlow() }}>
          <div className="flex h-[calc(100vh-48px)] w-[min(1500px,calc(100vw-48px))] flex-col overflow-hidden rounded-2xl border border-line bg-page shadow-2xl" role="dialog" aria-modal="true" aria-label="Agent 실행 흐름 상세">
            <div className="flex h-16 shrink-0 items-center justify-between border-b border-line bg-white px-6">
              <div>
                <div className="text-[16px] font-extrabold text-navy">Agent 실행 흐름</div>
                <div className="mt-0.5 text-[13px] text-g2">Level 3 상시 운영 · 노드를 누르면 그 단계의 근거 패널이 열립니다</div>
              </div>
              <button type="button" onClick={closeFlow} className="rounded-lg border border-line bg-white px-3 py-2 text-[12px] font-bold text-g1 hover:bg-soft">닫기 ✕</button>
            </div>
            <div className="relative min-h-0 flex-1">
              <Card className="flex h-full min-h-0 flex-col overflow-hidden rounded-none border-0">
                <div className="min-h-0 flex-1 bg-soft/40">
                  <ReactFlow nodeTypes={NODE_TYPES} nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.08 }} minZoom={0.55} maxZoom={1.5} nodesDraggable={false} nodesConnectable={false} onNodeClick={(_event, node) => selectNode(node.id)} onPaneClick={() => setDetailOpen(false)} deleteKeyCode={null} proOptions={{ hideAttribution: true }} aria-label="Agent 실행 흐름">
                    <Panel position="top-left" className="!m-2 flex items-center gap-3 rounded-lg border border-line bg-white/95 px-3.5 py-2.5 text-[11.5px] font-bold text-g2 shadow-sm">
                      <span className="flex items-center gap-1.5"><i className="h-px w-5 bg-slate-500" />현재 실행</span>
                    </Panel>
                    <Controls position="top-right" showInteractive={false} />
                  </ReactFlow>
                </div>
              </Card>
              {detailOpen && (
                <aside className="absolute right-4 top-4 z-10 max-h-[calc(100%-32px)] w-[min(620px,52%)] min-w-[500px] drop-shadow-2xl" data-testid="agent-execution-step-panel">
                  <button type="button" onClick={() => setDetailOpen(false)} className="absolute right-3 top-3 z-20 rounded-lg border border-line bg-white px-2.5 py-1.5 text-[12px] font-bold text-g1 shadow-sm hover:bg-soft">닫기 ✕</button>
                  <StepPanel detail={detail} step={selected} alarm={alarm} />
                </aside>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default AgentExecutionFlow
