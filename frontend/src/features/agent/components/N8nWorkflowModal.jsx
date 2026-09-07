import { useMemo, useState } from 'react'
import { Controls, Handle, MarkerType, Position, ReactFlow } from '@xyflow/react'
import WORKFLOWS from '../n8n/workflows.json'

// n8n 워크플로 읽기 전용 복제 화면 — deploy/n8n/*.json(팀 n8n에 배포된 정의)의 노드 위치·연결을 그대로 그린다.
// 실행 상태나 실제 실행 데이터는 표시하지 않는다(그건 전달 기록·감사 이력이 정본).
const WORKFLOW_META = {
  WF2: { title: 'WF2 · 이메일 알림', summary: 'Backend → Webhook 인증 → 페이로드 검증 → 이메일 발송 → 결과 콜백' },
  WF3: { title: 'WF3 · MES HOLD 요청', summary: 'Backend → Webhook 인증 → 페이로드 검증 → Kafka fdc.actions 발행 → 실패 시 콜백' },
  WF4: { title: 'WF4 · MES 결과 반영', summary: 'Kafka fdc.actions.result 수신 → 결과 검증 → Backend 결과 콜백' },
}

// 전달 기록이 SENT면 강조할 성공 경로(노드 이름은 배포 정의와 동일). 그 외 상태는 강조 없이 전체 정의만 보여 준다.
const SUCCESS_PATH = {
  WF2: ['Email Webhook', 'Verify Email Auth', 'Email Authenticated', 'Validate Email Payload', 'Email Payload Valid', 'Send Email', 'Build Email Callback', 'Email Callback Configured', 'Post Email Callback', 'Classify Email Callback', 'Email Callback Succeeded', 'Respond Email Accepted'],
  WF3: ['MES Webhook', 'Verify MES Auth', 'MES Authenticated', 'Validate MES Payload', 'MES Payload Valid', 'Prepare Kafka Event', 'Publish MES Hold', 'Respond MES Accepted'],
  WF4: ['MES Result Trigger', 'Validate MES Result', 'MES Result Valid', 'Build Result Callback', 'Result Callback Configured', 'Post Result Callback', 'Classify Result Callback'],
}
const CHANNEL_OF = { WF2: 'EMAIL', WF3: 'MES', WF4: 'MES' }

const highlightedPath = (key, action) => {
  const status = (action?.deliveries ?? []).find((delivery) => delivery.channel === CHANNEL_OF[key])?.status
  return status === 'SENT' ? SUCCESS_PATH[key] : null
}

const NODE_ICON = {
  webhook: { glyph: '⚡', tone: '#e11d48', label: 'Webhook' },
  kafkaTrigger: { glyph: '⚡', tone: '#e11d48', label: 'Kafka Trigger' },
  code: { glyph: '{ }', tone: '#f59e0b', label: 'Code' },
  if: { glyph: '⇄', tone: '#0f766e', label: 'IF' },
  kafka: { glyph: '⋮⋮', tone: '#1f2937', label: 'Kafka' },
  httpRequest: { glyph: '🌐', tone: '#4f46e5', label: 'HTTP Request' },
  emailSend: { glyph: '✉', tone: '#2563eb', label: 'Send Email' },
  respondToWebhook: { glyph: '↩', tone: '#e11d48', label: 'Respond' },
}

const outputLabel = (node, output) => {
  if (node.type === 'if') return output === 0 ? 'true' : 'false'
  if (node.onError === 'continueErrorOutput') return output === 0 ? 'Success' : 'Error'
  if (node.type === 'webhook') return 'POST'
  return undefined
}

const outputCount = (node) => (node.type === 'if' || node.onError === 'continueErrorOutput' ? 2 : 1)
const isTrigger = (node) => node.type === 'webhook' || node.type === 'kafkaTrigger'
const HANDLE_CLASS = '!h-2 !w-2 !border-0 !bg-slate-400'

function N8nNode({ data }) {
  const icon = NODE_ICON[data.type] ?? { glyph: '▢', tone: '#64748b', label: data.type }
  const outputs = data.outputs ?? 1
  const dimmed = data.focus === 'dim'
  const active = data.focus === 'active'
  return (
    <div className="flex w-[190px] flex-col items-center" style={{ opacity: dimmed ? 0.32 : 1 }}>
      <div
        className="relative flex h-[84px] w-[84px] items-center justify-center rounded-2xl border-[3px] bg-white text-[26px] font-extrabold"
        style={{ borderColor: active ? '#2563eb' : icon.tone, color: icon.tone, boxShadow: active ? '0 0 0 6px rgba(37,99,235,.15), 0 6px 16px rgba(15,23,42,.12)' : '0 2px 8px rgba(15,23,42,.08)' }}
        title={icon.label}
      >
        {!data.trigger && <Handle id="in" type="target" position={Position.Left} className={HANDLE_CLASS} />}
        {outputs === 1
          ? <Handle id="out0" type="source" position={Position.Right} className={HANDLE_CLASS} />
          : (
            <>
              <Handle id="out0" type="source" position={Position.Right} style={{ top: '30%' }} className={HANDLE_CLASS} />
              <Handle id="out1" type="source" position={Position.Right} style={{ top: '70%' }} className={HANDLE_CLASS} />
            </>
          )}
        {icon.glyph}
      </div>
      <div className="mt-2 text-center text-[13.5px] font-extrabold leading-5 text-ink">{data.name}</div>
      <div className="text-[11px] text-g2">{icon.label}</div>
    </div>
  )
}

const NODE_TYPES = { n8n: N8nNode }

function buildGraph(workflow, path) {
  const byName = new Map(workflow.nodes.map((node) => [node.name, node]))
  const onPath = path ? new Set(path) : null
  const pathEdge = (edge) => onPath && onPath.has(edge.source) && onPath.has(edge.target)
    && path.indexOf(edge.target) === path.indexOf(edge.source) + 1
  const nodes = workflow.nodes.map((node) => ({
    id: node.name,
    type: 'n8n',
    position: { x: node.position[0] * 1.5, y: node.position[1] * 1.5 },
    data: {
      name: node.name,
      type: node.type,
      outputs: outputCount(node),
      trigger: isTrigger(node),
      focus: !onPath ? 'plain' : onPath.has(node.name) ? 'active' : 'dim',
    },
    draggable: false,
    connectable: false,
  }))
  const edges = workflow.edges.map((edge, index) => {
    const active = pathEdge(edge)
    const dimmed = onPath && !active
    const color = active ? '#2563eb' : '#8095a9'
    return {
      id: `${edge.source}->${edge.target}#${index}`,
      source: edge.source,
      target: edge.target,
      sourceHandle: `out${edge.output}`,
      targetHandle: 'in',
      type: 'smoothstep',
      animated: active,
      label: outputLabel(byName.get(edge.source), edge.output),
      labelStyle: { fill: active ? '#1d4ed8' : '#64788b', fontSize: 12, fontWeight: 700, opacity: dimmed ? 0.35 : 1 },
      labelBgStyle: { fill: '#ffffff', fillOpacity: 0.95 },
      markerEnd: { type: MarkerType.ArrowClosed, color },
      style: { stroke: color, strokeWidth: active ? 2.6 : 1.5, opacity: dimmed ? 0.3 : 1 },
    }
  })
  return { nodes, edges }
}

export default function N8nWorkflowModal({ workflows = ['WF2', 'WF3', 'WF4'], initial = workflows[0], action = null, onClose }) {
  const [current, setCurrent] = useState(initial)
  const path = highlightedPath(current, action)
  const graph = useMemo(() => buildGraph(WORKFLOWS[current], path), [current, path])
  const meta = WORKFLOW_META[current]
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/45 p-6" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="flex h-[calc(100vh-48px)] w-[calc(100vw-48px)] flex-col overflow-hidden rounded-2xl border border-line shadow-2xl" style={{ backgroundColor: '#ffffff' }} role="dialog" aria-modal="true" aria-label="n8n 워크플로">
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-line bg-white px-6">
          <div>
            <div className="text-[16px] font-extrabold text-navy">n8n 워크플로 · {meta.title}</div>
            <div className="mt-0.5 text-[12.5px] text-g2">{meta.summary}{path ? ' · 파란 굵은 선 = 전달 기록 SENT 기준 실행 경로' : ' · 전달 기록이 완료(SENT)가 아니어서 경로 강조 없음'}</div>
          </div>
          <div className="flex items-center gap-2">
            {workflows.map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setCurrent(key)}
                className={`inline-flex h-8 items-center rounded-lg border px-3 text-[12px] font-bold ${current === key ? 'border-blue bg-tint-blue text-blue-hover' : 'border-field-line bg-white text-g2'}`}
              >
                {key}
              </button>
            ))}
            <button type="button" onClick={onClose} className="ml-2 rounded-lg border border-line bg-white px-3 py-2 text-[12px] font-bold text-g1 hover:bg-soft">닫기 ✕</button>
          </div>
        </div>
        <div className="min-h-0 flex-1" style={{ backgroundColor: '#ffffff', backgroundImage: 'radial-gradient(#d9e0ea 1px, transparent 1px)', backgroundSize: '18px 18px' }}>
          <ReactFlow
            key={current}
            nodeTypes={NODE_TYPES}
            nodes={graph.nodes}
            edges={graph.edges}
            fitView
            fitViewOptions={{ padding: 0.04 }}
            minZoom={0.3}
            maxZoom={1.6}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            proOptions={{ hideAttribution: true }}
          >
            <Controls position="top-right" showInteractive={false} />
          </ReactFlow>
        </div>
      </div>
    </div>
  )
}
