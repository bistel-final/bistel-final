import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAllAlarms } from '../../../shared/api/detection.js'
import { getChamberRelationsCore } from '../../../shared/api/knowledge.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import OntologyGraphCanvas from '../../../shared/components/ontology/OntologyGraphCanvas.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'
import {
  ONTOLOGY_NODE_META,
  buildOntologyOverviewLanes,
  graphRelationIds,
  hasDisplayableRelationships,
  mergeOntologyGraphs,
  ontologyAlarmScope,
  publicNodeDetails,
  summarizeOntologyAlarms,
} from '../../../shared/graph/ontology-graph.js'
import { DEFAULT_GRAPH_CHAMBER, GRAPH_CHAMBERS } from '../../../shared/graph/ontology-chambers.js'
import { parseOntologyFocus, resolveOntologyFocus } from '../ontology-focus-state.js'
const LOAD_ERROR = '온톨로지 관계를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.'

const controlledFocus = (params) => {
  const focus = parseOntologyFocus(params)
  if (focus.phase === 'ready' && !GRAPH_CHAMBERS.includes(focus.chamberId)) {
    return { phase: 'invalid', message: '지원하는 챔버가 아닌 온톨로지 링크입니다.' }
  }
  return focus
}

function GraphSummary({ graph, compact = false, scopeLabel = null }) {
  const counts = graph.nodes.reduce((result, node) => {
    result[node.label] = (result[node.label] ?? 0) + 1
    return result
  }, {})
  return (
    <div className={`rounded-[10px] border border-line bg-white px-4 py-3 ${compact ? '' : 'flex flex-wrap items-center gap-2'}`}>
      {compact && <div className="mb-2 text-[10px] font-extrabold text-faint">현재 그래프</div>}
      <div className={compact ? 'break-all font-mono text-[12px] font-extrabold text-navy' : 'contents'}>
        <span className={compact ? '' : 'font-mono text-[12px] font-extrabold text-navy'}>{scopeLabel ?? graph.root_node_id}</span>
      </div>
      <div className={`${compact ? 'mt-2' : ''} flex flex-wrap items-center gap-2`}>
        <span className="text-[11.5px] text-g2">관계 {graph.relationships.length}건</span>
        {Object.entries(counts).map(([label, count]) => (
          <span key={label} className="rounded-md bg-soft px-2 py-1 font-mono text-[10.5px] font-bold text-g1">
            {ONTOLOGY_NODE_META[label]?.shortLabel ?? label} {count}
          </span>
        ))}
      </div>
      <div className={`${compact ? 'mt-2' : 'ml-auto'} truncate font-mono text-[9.5px] text-faint`} title={graph.graph_revision ?? ''}>
        revision {graph.graph_revision ?? '미제공'}
      </div>
    </div>
  )
}

const formatOccurredAt = (value) => {
  if (!value) return '없음'
  const date = new Date(String(value).replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function NodeOperationalStatus({ state, onRetry }) {
  if (state.status === 'idle') {
    return <div className="text-[11px] text-g2">그래프 노드를 선택하면 운영 요약을 확인할 수 있습니다.</div>
  }
  if (state.status === 'loading') return <div className="text-[11px] text-g2">알람 상태 집계 중…</div>
  if (state.status === 'unsupported') {
    return (
      <div className="text-[11px] text-g2">
        이 subgraph 관계만으로 집계 범위를 확정할 수 없습니다.
      </div>
    )
  }
  if (state.status === 'error') {
    return (
      <div className="flex items-center justify-between gap-3 text-[11px] text-g2">
        <span>알람 상태를 불러오지 못했습니다.</span>
        <button type="button" onClick={onRetry} className="font-bold text-blue">다시 시도</button>
      </div>
    )
  }
  if (state.status !== 'success') return null
  const summary = state.summary
  const mockHasNoMatch = state.mock && summary.total === 0
  const severity = mockHasNoMatch
    ? ['MOCK 대응 데이터 없음', 'text-g1 bg-soft']
    : summary.oos > 0
      ? ['OOS 감지', 'text-red-600 bg-red-50']
      : summary.ooc > 0
        ? ['OOC 감지', 'text-amber-600 bg-amber-50']
        : ['알람 없음', 'text-emerald-700 bg-emerald-50']
  return (
    <div data-testid="ontology-node-operational-status">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-[10px] font-bold text-faint">선택 노드 운영 요약</div>
          <div className="mt-0.5 text-[9px] text-faint">{state.basis} · 저장 데이터 전체</div>
        </div>
        <span className={`rounded-full px-2 py-1 text-[9.5px] font-extrabold ${severity[1]}`}>{severity[0]}</span>
      </div>
      <div className="mt-2 grid grid-cols-4 gap-2">
        {[
          ['ALARM', summary.total, 'text-ink'],
          ['OOS', summary.oos, 'text-red-600'],
          ['OOC', summary.ooc, 'text-amber-600'],
          ['ACTION', summary.actions, 'text-blue'],
        ].map(([label, value, color]) => (
          <div key={label} className="rounded-lg bg-soft px-2.5 py-2 text-center">
            <div className="text-[9px] font-bold text-faint">{label}</div>
            <div className={`mt-0.5 font-mono text-[15px] font-extrabold ${color}`}>{value}</div>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap justify-between gap-2 text-[9.5px] text-g2">
        <span>최근 발생 · {formatOccurredAt(summary.latest_occurred_at)}</span>
        <span>승인 대기 조치 · {summary.pending_actions}건</span>
      </div>
      {mockHasNoMatch && (
        <div className="mt-2 rounded-md bg-tint-amber px-2.5 py-2 text-[9.5px] font-semibold text-tint-amber-text">
          현재 mock에는 canonical ID와 일치하는 항목이 없습니다. 실 API 결과가 아닙니다.
        </div>
      )}
      {state.partial && (
        <div className="mt-2 rounded-md bg-tint-amber px-2.5 py-2 text-[9.5px] font-semibold text-tint-amber-text">
          페이지 조회 상한에 도달해 일부 데이터만 집계했습니다.
        </div>
      )}
    </div>
  )
}

function NodeDetail({ graph, node, isSelected, alarmState, onRetryAlarms }) {
  if (!node) {
    return (
      <Card className="flex min-h-40 items-center justify-center p-4 text-center text-[12px] text-g2">
        그래프 노드를 선택하면 공개 속성을 확인할 수 있습니다.
      </Card>
    )
  }
  const details = publicNodeDetails(node)
  const meta = ONTOLOGY_NODE_META[node.label]
  const relationCount = graph.relationships.filter(
    (relationship) => relationship.source === node.id || relationship.target === node.id,
  ).length
  const isQueryRoot = graph.root_node_id === node.id
  return (
    <Card className="flex flex-col gap-4 p-4" data-testid="ontology-node-detail">
      <div>
        <div className="text-[10px] font-extrabold tracking-[.08em]" style={{ color: meta?.color }}>
          {meta?.shortLabel ?? node.label}
        </div>
        <div className="mt-1 break-all font-mono text-[15px] font-extrabold text-ink">{node.display_name}</div>
        <div className="mt-1 break-all font-mono text-[10.5px] text-g2">{node.business_id}</div>
        <div className="mt-2 flex flex-wrap gap-1.5 text-[9.5px] font-bold">
          {isSelected && <span className="rounded-full bg-tint-blue px-2 py-1 text-blue">현재 선택</span>}
          <span className="rounded-full bg-soft px-2 py-1 text-g1">
            {isQueryRoot ? '조회 기준 챔버' : '연결 노드'}
          </span>
          <span className="rounded-full bg-soft px-2 py-1 text-g1">직접 관계 {relationCount}건</span>
        </div>
      </div>
      <div className="border-t border-cell-line pt-3">
        <div className="mb-2 text-[10px] font-bold text-faint">공개 속성</div>
        {details.length ? (
          <div className="flex flex-wrap gap-2">
            {details.map(({ key, value }) => (
              <div key={key} className="min-w-[150px] rounded-lg bg-soft px-3 py-2">
                <div className="text-[9.5px] font-bold text-g2">{key}</div>
                <div className="mt-0.5 break-all font-mono text-[11px] font-semibold text-ink">{value}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-[11.5px] text-g2">추가 공개 속성이 없습니다.</div>
        )}
      </div>
      <div className="border-t border-cell-line pt-3">
        <NodeOperationalStatus state={alarmState} onRetry={onRetryAlarms} />
      </div>
    </Card>
  )
}

function FocusNotice({ focus }) {
  if (!focus || ['none', 'ready'].includes(focus.phase)) return null
  if (focus.phase === 'found') {
    if (focus.kind === 'impact') {
      return (
        <div className="rounded-[10px] border border-tint-blue-line bg-tint-blue px-4 py-3">
          <div className="text-[12px] font-extrabold text-blue">Agent 영향 범위</div>
          <div className="mt-2 flex flex-wrap gap-2 text-[10.5px] font-bold">
            {focus.directNodes.map((node) => (
              <span key={node.id} className="rounded-md border border-blue/20 bg-white px-2 py-1 text-blue">{node.business_id}</span>
            ))}
            {focus.checkNodes.map((node) => (
              <span key={node.id} className="rounded-md border border-tint-amber-line bg-white px-2 py-1 text-tint-amber-text">확인 필요 · {node.business_id}</span>
            ))}
          </div>
        </div>
      )
    }
    return (
      <div className="rounded-[10px] border border-tint-blue-line bg-tint-blue px-4 py-3">
        <div className="text-[12px] font-extrabold text-blue">Agent 근거 관계 복원 완료</div>
        <div className="mt-1 font-mono text-[10.5px] text-g1">
          {focus.relation.id} · {focus.relation.type} · rev {focus.graphRevision}
        </div>
      </div>
    )
  }
  const message = focus.phase === 'revision-mismatch'
    ? `요청한 그래프 revision과 현재 revision이 달라 관계를 강조할 수 없습니다. (현재 ${focus.actualRevision})`
    : '요청한 그래프 관계를 현재 revision에서 찾을 수 없습니다.'
  return (
    <div className="rounded-[10px] border border-tint-amber-line bg-tint-amber px-4 py-3 text-[12px] font-bold text-tint-amber-text">
      {message}
    </div>
  )
}

const OverviewGroup = ({ nodes, selectedNodeId, onSelectNode }) => (
  <div className="flex min-h-14 min-w-0 flex-wrap content-center justify-center gap-1.5 rounded-lg px-2 py-2">
      {nodes.map((node) => {
        const selected = node.id === selectedNodeId
        const color = ONTOLOGY_NODE_META[node.label]?.color ?? '#64748b'
        const className = `rounded-md border px-2 py-1.5 text-center font-mono text-[9.5px] font-bold transition ${
          selected
            ? 'bg-blue text-white shadow-sm'
            : 'hover:border-blue/50 hover:bg-tint-blue'
        }`
        return (
          <button
            key={node.id}
            type="button"
            className={className}
            style={selected ? undefined : { borderColor: `${color}55`, backgroundColor: `${color}0d`, color }}
            onClick={() => onSelectNode(node)}
          >
            {node.business_id}
          </button>
        )
      })}
  </div>
)

const OverviewArrow = () => <div className="text-center text-[17px] font-extrabold text-blue">→</div>

const OVERVIEW_GRID = 'grid min-w-[1120px] grid-cols-[1.15fr_28px_1fr_28px_.9fr_28px_1.45fr_28px_1.55fr_28px_1.5fr] items-center gap-2'

function OntologyOverview({ graph, selectedNodeId, onSelectNode }) {
  const lanes = useMemo(() => buildOntologyOverviewLanes([graph]), [graph])
  if (lanes.length === 0) return null
  return (
    <Card className="p-4" data-testid="ontology-overview">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="text-[13px] font-extrabold text-ink">전체 구조 안내</div>
          <div className="mt-1 text-[10.5px] text-g2">
            두 모델의 실제 관계 흐름을 비교하고 챔버를 선택해 아래 그래프를 탐색합니다.
          </div>
        </div>
      </div>
      <div className="mt-3 overflow-x-auto pb-1">
        <div className={`${OVERVIEW_GRID} px-3 pb-2 text-center text-[9px] font-extrabold tracking-[.08em] text-faint`}>
          <span>MODEL</span><span /><span>PROCESS STEP</span><span /><span>AREA</span><span />
          <span>EQUIPMENT</span><span /><span>CHAMBER</span><span /><span>PARAMETER</span>
        </div>
        <div className="flex min-w-[1120px] flex-col gap-2.5">
          {lanes.map((lane) => (
            <div key={lane.model.id} className={`${OVERVIEW_GRID} rounded-[10px] border border-cell-line bg-soft/60 px-3 py-2.5`}>
              <OverviewGroup nodes={[lane.model]} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
              <OverviewArrow />
              <OverviewGroup nodes={lane.steps} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
              <OverviewArrow />
              <OverviewGroup nodes={lane.areas} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
              <OverviewArrow />
              <OverviewGroup nodes={lane.equipments} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
              <OverviewArrow />
              <OverviewGroup nodes={lane.chambers} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
              <OverviewArrow />
              <OverviewGroup nodes={lane.parameters} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
            </div>
          ))}
        </div>
      </div>
    </Card>
  )
}

function OntologyPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const focus = useMemo(() => controlledFocus(searchParams), [searchParams])
  const [selectedChamber, setSelectedChamber] = useState(
    focus.phase === 'ready' ? focus.chamberId : '',
  )
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState({ status: 'loading', graph: null, graphsByChamber: {}, partial: false })
  const [selectedNode, setSelectedNode] = useState(null)
  const [alarmAttempt, setAlarmAttempt] = useState(0)
  const [alarmState, setAlarmState] = useState({ requestKey: null, status: 'idle', summary: null, basis: null })
  const explicitChamber = focus.phase === 'ready' ? focus.chamberId : selectedChamber
  const requestChamber = explicitChamber || DEFAULT_GRAPH_CHAMBER
  const graph = useMemo(() => {
    if (!state.graph) return null
    if (!explicitChamber) return state.graph
    const rootNodeId = `Chamber:${explicitChamber}`
    return state.graph.nodes.some((node) => node.id === rootNodeId)
      ? { ...state.graph, root_node_id: rootNodeId }
      : state.graph
  }, [explicitChamber, state.graph])
  const activeSelectedNode = focus.phase === 'ready' ? null : selectedNode

  useEffect(() => {
    if (focus.phase === 'invalid') return undefined
    let active = true
    const orderedChambers = [requestChamber, ...GRAPH_CHAMBERS.filter((chamberId) => chamberId !== requestChamber)]
    Promise.allSettled(orderedChambers.map((chamberId) => getChamberRelationsCore(chamberId))).then((results) => {
      if (!active) return
      const requestedResult = results[0]
      if (requestedResult.status !== 'fulfilled') {
        setState({ status: 'error', graph: null, graphsByChamber: {}, partial: false })
        return
      }
      const graphsByChamber = {}
      const responses = []
      results.forEach((result, index) => {
        if (result.status !== 'fulfilled') return
        graphsByChamber[orderedChambers[index]] = result.value
        responses.push(result.value)
      })
      const merged = mergeOntologyGraphs(responses, `Chamber:${requestChamber}`)
      setState({
        status: hasDisplayableRelationships(merged) ? 'success' : 'empty',
        graph: merged,
        graphsByChamber,
        partial: responses.length !== orderedChambers.length,
      })
      if (focus.phase !== 'ready') {
        setSelectedNode(explicitChamber
          ? merged?.nodes.find((node) => node.id === `Chamber:${explicitChamber}`) ?? null
          : null)
      }
    })
    return () => {
      active = false
    }
  }, [attempt, explicitChamber, focus.phase, requestChamber])

  useEffect(() => {
    const scope = ontologyAlarmScope(graph, activeSelectedNode)
    if (!activeSelectedNode || !scope) return undefined
    let active = true
    const requestKey = `${activeSelectedNode.id}:${alarmAttempt}`
    Promise.all(scope.requests.map((params) => getAllAlarms(params))).then(
      (responses) => {
        if (active) {
          const uniqueAlarms = new Map()
          for (const response of responses) {
            for (const alarm of response.items ?? []) {
              const key = alarm.alarm_id ?? `${alarm.source ?? ''}:${alarm.occurred_at ?? ''}:${uniqueAlarms.size}`
              uniqueAlarms.set(key, alarm)
            }
          }
          setAlarmState({
            requestKey,
            status: 'success',
            summary: summarizeOntologyAlarms([...uniqueAlarms.values()]),
            basis: scope.basis,
            mock: responses.some((response) => response.mock),
            partial: responses.some((response) => response.partial),
          })
        }
      },
      () => {
        if (active) setAlarmState({ requestKey, status: 'error', summary: null, basis: scope.basis })
      },
    )
    return () => {
      active = false
    }
  }, [activeSelectedNode, alarmAttempt, graph])

  const selectedAlarmScope = ontologyAlarmScope(graph, activeSelectedNode)
  const alarmRequestKey = activeSelectedNode ? `${activeSelectedNode.id}:${alarmAttempt}` : null
  const displayedAlarmState = !activeSelectedNode
    ? { status: 'idle' }
    : !selectedAlarmScope
      ? { status: 'unsupported' }
      : alarmState.requestKey === alarmRequestKey
        ? alarmState
        : { status: 'loading', basis: selectedAlarmScope.basis }

  const resolvedFocus = useMemo(() => {
    if (focus.phase !== 'ready' || state.status !== 'success' || !graph) return focus
    return resolveOntologyFocus(graph, focus)
  }, [focus, graph, state.status])
  const focusedRelationIds = useMemo(
    () => new Set(resolvedFocus.phase === 'found' && resolvedFocus.kind === 'relation' ? [resolvedFocus.relation.id] : []),
    [resolvedFocus],
  )
  const selectedChamberScopeRelationIds = useMemo(() => {
    if (activeSelectedNode?.label !== 'Chamber') return new Set()
    return graphRelationIds(state.graphsByChamber[activeSelectedNode.business_id])
  }, [activeSelectedNode, state.graphsByChamber])
  const focusedImpactNodeIds = useMemo(
    () => new Set(resolvedFocus.phase === 'found' && resolvedFocus.kind === 'impact'
      ? resolvedFocus.directNodes.map((node) => node.id)
      : []),
    [resolvedFocus],
  )
  const checkRequiredNodeIds = useMemo(
    () => new Set(resolvedFocus.phase === 'found' && resolvedFocus.kind === 'impact'
      ? resolvedFocus.checkNodes.map((node) => node.id)
      : []),
    [resolvedFocus],
  )
  const status = focus.phase === 'invalid' ? 'invalid' : state.status
  const displayedNode = activeSelectedNode && graph?.nodes.some((node) => node.id === activeSelectedNode.id)
    ? activeSelectedNode
    : focus.phase === 'ready'
      ? graph?.nodes.find((node) => node.id === graph.root_node_id) ?? null
      : null

  const changeChamber = (chamberId) => {
    setSelectedChamber(chamberId)
    setSelectedNode(chamberId
      ? state.graph?.nodes.find((node) => node.id === `Chamber:${chamberId}`) ?? null
      : null)
    setSearchParams({}, { replace: true })
  }

  const selectChamber = (event) => changeChamber(event.target.value)

  const selectGraphNode = (node) => {
    setSelectedNode(node)
    if (node && focus.phase === 'ready') setSearchParams({}, { replace: true })
  }

  const selectOverviewNode = (node) => {
    setSelectedChamber('')
    setSelectedNode(node)
    setSearchParams({}, { replace: true })
  }

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div>
          <div className="text-[20px] font-extrabold text-ink">온톨로지</div>
          <div className="mt-1 text-[11.5px] text-g2">전체 설비·공정·파라미터 관계에서 필요한 챔버를 탐색합니다</div>
        </div>
        <label className="flex items-center gap-2 text-[11px] font-bold text-g2">
          CHAMBER
          <select
            value={explicitChamber}
            onChange={selectChamber}
            className="h-9 min-w-[180px] rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] font-bold text-ink"
            aria-label="온톨로지 챔버 선택"
          >
            <option value="">전체 구조</option>
            {GRAPH_CHAMBERS.map((chamberId) => (
              <option key={chamberId} value={chamberId}>
                {chamberId}
              </option>
            ))}
          </select>
        </label>
      </div>

      {status === 'invalid' && (
        <ErrorState title="올바르지 않은 온톨로지 링크입니다" detail={focus.message} />
      )}
      {status === 'loading' && <LoadingState message={`${explicitChamber || '전체'} 관계를 불러오는 중…`} />}
      {status === 'error' && (
        <ErrorState
          title="온톨로지 조회 오류"
          detail={LOAD_ERROR}
          onRetry={() => {
            setState({ status: 'loading', graph: null, graphsByChamber: {}, partial: false })
            setAttempt((value) => value + 1)
          }}
        />
      )}
      {status === 'empty' && (
        <EmptyState title="표시 가능한 관계가 없습니다" description={`${explicitChamber || '전체 구조'}의 관계 데이터가 없습니다.`} />
      )}
      {status === 'success' && graph && (
        <div className="flex flex-col gap-4">
          <FocusNotice focus={resolvedFocus} />
          {state.partial && (
            <div className="rounded-[10px] border border-tint-amber-line bg-tint-amber px-4 py-3 text-[12px] font-bold text-tint-amber-text">
              일부 보조 챔버 관계를 불러오지 못해 조회한 챔버와 확인 가능한 관계만 표시합니다.
            </div>
          )}
          <OntologyOverview graph={graph} selectedNodeId={activeSelectedNode?.id ?? null} onSelectNode={selectOverviewNode} />
          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <OntologyGraphCanvas graph={graph} focusedRelationIds={focusedRelationIds}
              scopeRelationIds={selectedChamberScopeRelationIds}
              impactNodeIds={focusedImpactNodeIds} checkRequiredNodeIds={checkRequiredNodeIds}
              selectedNodeId={activeSelectedNode?.id ?? null} onSelectNode={selectGraphNode} viewport="page"
              emphasizeRoot={Boolean(explicitChamber)} />
            <div className="flex flex-col gap-3 xl:sticky xl:top-4">
              <GraphSummary graph={graph} compact scopeLabel={explicitChamber ? null : '전체 온톨로지'} />
              <NodeDetail graph={graph} node={displayedNode} isSelected={Boolean(activeSelectedNode)}
                alarmState={displayedAlarmState} onRetryAlarms={() => setAlarmAttempt((value) => value + 1)} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default OntologyPage
