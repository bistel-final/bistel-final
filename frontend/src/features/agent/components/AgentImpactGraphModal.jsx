import { useEffect, useMemo, useState } from 'react'
import { fmtDateTime } from '../../../shared/api/format.js'
import { getAllAlarms } from '../../../shared/api/detection.js'
import { getChamberRelationsCore } from '../../../shared/api/knowledge.js'
import OntologyGraphCanvas from '../../../shared/components/ontology/OntologyGraphCanvas.jsx'
import {
  ONTOLOGY_NODE_META,
  hasDisplayableRelationships,
  normalizeOntologyGraph,
  ontologyAlarmScope,
  publicNodeDetails,
  summarizeOntologyAlarms,
} from '../../../shared/graph/ontology-graph.js'

const EMPTY_STATE = Object.freeze({ phase: 'loading', graph: null, message: null })

const nodeImpact = (node, selection, impactScope) => {
  if (!node) return null
  if (selection.directNodeIds.includes(node.id)) {
    return {
      label: '영향 노드',
      className: 'border-tint-blue-line bg-tint-blue text-blue',
      detail: impactScope?.summary ?? 'Agent가 현재 incident의 영향 범위로 분류했습니다.',
    }
  }
  if (selection.checkNodeIds.includes(node.id)) {
    const item = impactScope?.check_required?.find((candidate) => candidate.source_id === node.business_id)
    return {
      label: '확인 필요',
      className: 'border-tint-amber-line bg-tint-amber text-tint-amber-text',
      detail: item?.relation
        ? `${item.relation} 관계를 따라 추가 확인이 필요한 노드입니다.`
        : '직접 영향으로 확정하지 않고 추가 확인 대상으로 분류했습니다.',
    }
  }
  return {
    label: '연결 문맥',
    className: 'border-line bg-soft text-g1',
    detail: '영향 노드의 관계를 이해하기 위해 함께 표시한 문맥 노드입니다.',
  }
}

function ImpactNodePanel({ graph, node, selection, impactScope, alarmState }) {
  if (!node) {
    return <div className="p-5 text-[13px] text-g2">그래프 노드를 선택하면 근거와 운영 데이터를 확인할 수 있습니다.</div>
  }
  const meta = ONTOLOGY_NODE_META[node.label]
  const impact = nodeImpact(node, selection, impactScope)
  const details = publicNodeDetails(node)
  const relationCount = graph.relationships.filter(
    (relationship) => relationship.source === node.id || relationship.target === node.id,
  ).length
  const summary = alarmState.summary

  return (
    <div className="flex flex-col gap-4 p-5" data-testid="agent-impact-node-panel">
      <div>
        <div className="text-[11px] font-extrabold tracking-[.08em]" style={{ color: meta?.color }}>
          {meta?.shortLabel ?? node.label}
        </div>
        <div className="mt-1 break-all font-mono text-[17px] font-extrabold text-ink">{node.display_name}</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <span className={`rounded-full border px-2.5 py-1 text-[10.5px] font-extrabold ${impact.className}`}>
            {impact.label}
          </span>
          <span className="rounded-full border border-line bg-soft px-2.5 py-1 text-[10.5px] font-bold text-g1">
            연결 관계 {relationCount}건
          </span>
        </div>
      </div>

      <section className="border-t border-cell-line pt-3">
        <div className="text-[11px] font-extrabold text-navy">Agent 판단 연결</div>
        <div className="mt-2 rounded-lg bg-soft px-3 py-2.5 text-[12.5px] leading-5 text-g1">{impact.detail}</div>
      </section>

      <section className="border-t border-cell-line pt-3">
        <div className="mb-2 text-[11px] font-extrabold text-navy">공개 속성</div>
        <div className="grid grid-cols-2 gap-2">
          {details.map(({ key, value }) => (
            <div key={key} className="min-w-0 rounded-lg bg-soft px-3 py-2">
              <div className="text-[9.5px] font-bold text-g2">{key}</div>
              <div className="mt-1 break-all font-mono text-[11.5px] font-semibold text-ink">{value}</div>
            </div>
          ))}
          {details.length === 0 && <div className="col-span-2 text-[11.5px] text-g2">추가 공개 속성이 없습니다.</div>}
        </div>
      </section>

      <section className="border-t border-cell-line pt-3">
        <div className="flex items-end justify-between gap-2">
          <div>
            <div className="text-[11px] font-extrabold text-navy">선택 노드 운영 요약</div>
            <div className="mt-1 text-[10px] text-g2">{alarmState.basis ?? '집계 범위 확인 중'} · 저장 데이터 전체</div>
          </div>
          {alarmState.partial && <span className="text-[9.5px] font-bold text-tint-amber-text">일부 집계</span>}
        </div>
        {alarmState.phase === 'loading' && <div className="mt-3 text-[12px] text-g2">알람 상태 집계 중…</div>}
        {alarmState.phase === 'unsupported' && <div className="mt-3 text-[12px] text-g2">이 관계만으로 집계 범위를 확정할 수 없습니다.</div>}
        {alarmState.phase === 'error' && <div className="mt-3 text-[12px] text-red">운영 데이터를 불러오지 못했습니다.</div>}
        {alarmState.phase === 'success' && summary && (
          <>
            <div className="mt-3 grid grid-cols-4 gap-2">
              {[
                ['ALARM', summary.total, 'text-ink'],
                ['OOS', summary.oos, 'text-red'],
                ['OOC', summary.ooc, 'text-tint-amber-text'],
                ['ACTION', summary.actions, 'text-blue'],
              ].map(([label, value, color]) => (
                <div key={label} className="rounded-lg bg-soft px-2 py-2.5 text-center">
                  <div className="text-[9px] font-bold text-g2">{label}</div>
                  <div className={`mt-1 font-mono text-[16px] font-extrabold ${color}`}>{value}</div>
                </div>
              ))}
            </div>
            <div className="mt-3 flex flex-col gap-1 text-[10.5px] text-g2">
              <span>최근 발생 · {summary.latest_occurred_at ? fmtDateTime(summary.latest_occurred_at) : '없음'}</span>
              <span>승인 대기 조치 · {summary.pending_actions}건</span>
            </div>
          </>
        )}
      </section>
    </div>
  )
}

function AgentImpactGraphModal({ onClose, selection, impactScope }) {
  const [state, setState] = useState(EMPTY_STATE)
  const [selectedNode, setSelectedNode] = useState(null)
  const [alarmState, setAlarmState] = useState({ requestKey: null, phase: 'idle', summary: null, basis: null })
  const directNodeIds = useMemo(() => new Set(selection?.directNodeIds ?? []), [selection])
  const checkNodeIds = useMemo(() => new Set(selection?.checkNodeIds ?? []), [selection])

  useEffect(() => {
    let active = true
    getChamberRelationsCore(selection.chamberId).then(
      (response) => {
        if (!active) return
        const graph = normalizeOntologyGraph(response)
        const availableNodeIds = new Set(graph?.nodes.map((node) => node.id) ?? [])
        const requestedNodeIds = [...selection.directNodeIds, ...selection.checkNodeIds]
        const complete = requestedNodeIds.every((nodeId) => availableNodeIds.has(nodeId))
        if (!hasDisplayableRelationships(graph) || graph.graph_revision !== selection.graphRevision || !complete) {
          setState({
            phase: 'error',
            graph: null,
            message: '현재 그래프 버전에서 Agent 영향 노드를 정확히 복원할 수 없습니다.',
          })
          return
        }
        setState({ phase: 'success', graph, message: null })
        setSelectedNode(
          graph.nodes.find((node) => selection.directNodeIds.includes(node.id))
            ?? graph.nodes.find((node) => node.id === graph.root_node_id)
            ?? null,
        )
      },
      () => {
        if (active) {
          setState({ phase: 'error', graph: null, message: '온톨로지 관계를 불러오지 못했습니다.' })
        }
      },
    )
    return () => {
      active = false
    }
  }, [selection])

  useEffect(() => {
    if (state.phase !== 'success' || !state.graph || !selectedNode) return undefined
    const scope = ontologyAlarmScope(state.graph, selectedNode)
    if (!scope) return undefined
    let active = true
    const requestKey = selectedNode.id
    Promise.all(scope.requests.map((params) => getAllAlarms(params))).then(
      (responses) => {
        if (!active) return
        const alarms = new Map()
        for (const response of responses) {
          for (const alarm of response.items ?? []) {
            const key = alarm.alarm_id ?? `${alarm.source ?? ''}:${alarm.occurred_at ?? ''}:${alarms.size}`
            alarms.set(key, alarm)
          }
        }
        setAlarmState({
          requestKey,
          phase: 'success',
          summary: summarizeOntologyAlarms([...alarms.values()]),
          basis: scope.basis,
          partial: responses.some((response) => response.partial),
        })
      },
      () => {
        if (active) setAlarmState({ requestKey, phase: 'error', summary: null, basis: scope.basis })
      },
    )
    return () => {
      active = false
    }
  }, [selectedNode, state.graph, state.phase])

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  const selectedScope = state.graph && selectedNode ? ontologyAlarmScope(state.graph, selectedNode) : null
  const displayedAlarmState = !selectedNode
    ? { phase: 'idle' }
    : !selectedScope
      ? { phase: 'unsupported' }
      : alarmState.requestKey === selectedNode.id
        ? alarmState
        : { phase: 'loading', basis: selectedScope.basis }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-6"
      role="presentation"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}
    >
      <div
        className="flex h-[calc(100vh-48px)] w-[min(1500px,calc(100vw-48px))] flex-col overflow-hidden rounded-2xl border border-line bg-page shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-label="Agent 영향 범위 크게 보기"
      >
        <div className="flex min-h-16 shrink-0 items-center justify-between gap-4 border-b border-line bg-white px-6 py-3">
          <div>
            <div className="text-[18px] font-extrabold text-navy">Agent 영향 범위</div>
            <div className="mt-1 text-[12.5px] text-g2">
              {selection.chamberId} 기준 · 청색은 영향 노드, 황갈색 점선은 추가 확인 노드입니다.
            </div>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg border border-line bg-white px-3 py-2 text-[12px] font-bold text-g1 hover:bg-soft">
            닫기 ✕
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col bg-white">
          <div className="flex flex-wrap gap-2 border-b border-line bg-white px-6 py-3 text-[11.5px] font-bold">
            {selection.directNodeIds.map((nodeId) => (
              <span key={nodeId} className="rounded-md border border-blue/20 bg-tint-blue px-2.5 py-1 text-blue">
                {nodeId.split(':').slice(1).join(':')}
              </span>
            ))}
            {selection.checkNodeIds.map((nodeId) => (
              <span key={nodeId} className="rounded-md border border-tint-amber-line bg-tint-amber px-2.5 py-1 text-tint-amber-text">
                확인 필요 · {nodeId.split(':').slice(1).join(':')}
              </span>
            ))}
          </div>

          <div className="min-h-0 flex-1 bg-white">
            {state.phase === 'loading' && (
              <div className="flex h-full items-center justify-center bg-white text-[14px] font-semibold text-g1">
                영향 범위 관계를 불러오는 중…
              </div>
            )}
            {state.phase === 'error' && (
              <div className="flex h-full flex-col items-center justify-center bg-white px-6 text-center">
                <div className="text-[17px] font-extrabold text-navy">영향 범위를 표시할 수 없습니다</div>
                <div className="mt-2 text-[13px] text-g1">{state.message}</div>
              </div>
            )}
            {state.phase === 'success' && state.graph && (
              <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_380px]">
                <OntologyGraphCanvas
                  graph={state.graph}
                  impactNodeIds={directNodeIds}
                  checkRequiredNodeIds={checkNodeIds}
                  selectedNodeId={selectedNode?.id ?? null}
                  onSelectNode={setSelectedNode}
                  viewport="modal"
                />
                <aside className="min-h-0 overflow-y-auto border-l border-line bg-white">
                  <ImpactNodePanel
                    graph={state.graph}
                    node={selectedNode}
                    selection={selection}
                    impactScope={impactScope}
                    alarmState={displayedAlarmState}
                  />
                </aside>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default AgentImpactGraphModal
