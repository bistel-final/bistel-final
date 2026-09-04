import { useEffect, useMemo, useRef, useState } from 'react'
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
  PUBLIC_NODE_PROPERTIES,
  annotateWaferAlarmHints,
  alarmsForOntologyScope,
  attachWaferAlarmContext,
  buildLotContextGraph,
  buildOntologyOverviewLanes,
  hasDisplayableRelationships,
  mergeOntologyGraphs,
  lotOptionsForChamber,
  waferOptionsForLot,
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
        구조 revision {graph.graph_revision ? String(graph.graph_revision).slice(0, 12) : '미제공'}
      </div>
    </div>
  )
}

const alarmIdentity = (alarm, index = 0) => `${alarm?.source ?? 'UNKNOWN'}:${alarm?.alarm_id ?? `${alarm?.occurred_at ?? 'unknown'}:${index}`}`

const formatOccurredAt = (value) => {
  if (!value) return '없음'
  const date = new Date(String(value).replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return String(value)
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const byType = Object.fromEntries(parts.filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]))
  return `${byType.month}-${byType.day} ${byType.hour}:${byType.minute}`
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
  const incident = Boolean(state.incident)
  const lot = Boolean(state.lot)
  const showActions = summary.actions > 0 || summary.pending_actions > 0
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
          <div className="text-[10px] font-bold text-faint">{lot ? '선택 LOT 알람 요약' : incident ? '선택 Incident 알람 요약' : '선택 노드 운영 요약'}</div>
          <div className="mt-0.5 text-[9px] text-faint">{state.basis} · 저장 데이터 전체</div>
        </div>
        <span className={`rounded-full px-2 py-1 text-[9.5px] font-extrabold ${severity[1]}`}>{severity[0]}</span>
      </div>
      <div className={`mt-2 grid gap-2 ${showActions ? 'grid-cols-4' : 'grid-cols-3'}`}>
        {[
          ['ALARM', summary.total, 'text-ink'],
          ['OOS', summary.oos, 'text-red-600'],
          ['OOC', summary.ooc, 'text-amber-600'],
          ...(showActions ? [['ACTION', summary.actions, 'text-blue']] : []),
        ].map(([label, value, color]) => (
          <div key={label} className="rounded-lg bg-soft px-2.5 py-2 text-center">
            <div className="text-[9px] font-bold text-faint">{label}</div>
            <div className={`mt-0.5 font-mono text-[15px] font-extrabold ${color}`}>{value}</div>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap justify-between gap-2 text-[9.5px] text-g2">
        <span>최근 발생 · {formatOccurredAt(summary.latest_occurred_at)}</span>
        {showActions && <span>승인 대기 조치 · {summary.pending_actions}건</span>}
      </div>
      {mockHasNoMatch && (
        <div className="mt-2 rounded-md bg-tint-amber px-2.5 py-2 text-[9.5px] font-semibold text-tint-amber-text">
          현재 mock에는 canonical ID와 일치하는 항목이 없습니다. 실 API 결과가 아닙니다.
        </div>
      )}
      {state.partial && (
        <div className="mt-2 rounded-md bg-tint-amber px-2.5 py-2 text-[9.5px] font-semibold text-tint-amber-text">
          일부 알람 조회에 실패했거나 페이지 조회 상한에 도달해, 확인 가능한 데이터만 집계했습니다.
        </div>
      )}
    </div>
  )
}

function WaferAlarmContext({ node, state, onRetry }) {
  if (state.status === 'loading') return <div className="text-[10.5px] text-g2">Wafer 알람 context를 조회하는 중입니다.</div>
  if (state.status === 'error') return <button type="button" onClick={onRetry} className="text-[10.5px] font-bold text-blue">Wafer 알람 context 다시 시도</button>
  if (state.status !== 'success') return <div className="text-[10.5px] text-g2">연결 가능한 Wafer 알람이 없습니다.</div>
  if ((state.alarms ?? []).length === 0) return <div className="text-[10.5px] text-g2">이 Wafer 처리이력에 연결된 알람이 없습니다.</div>
  const processHistoryByKey = new Map((node.properties?.process_history ?? []).map((history) => [
    `${history.chamber_id ?? ''}:${history.lot_hist_id ?? ''}`,
    history,
  ]))
  const alarmGroups = [...state.alarms.reduce((groups, alarm) => {
    const key = `${alarm.chamber_id ?? ''}:${alarm.lot_hist_id ?? ''}`
    const group = groups.get(key) ?? { key, chamberId: alarm.chamber_id, history: processHistoryByKey.get(key), alarms: [] }
    group.alarms.push(alarm)
    groups.set(key, group)
    return groups
  }, new Map()).values()]
  return (
    <div>
      <div className="text-[10px] font-bold text-faint">Wafer 알람 · 관련 Parameter</div>
      <div className="mt-2 flex flex-col gap-2">
        {alarmGroups.map((group) => (
          <div key={group.key} className="rounded-md border border-cell-line bg-white px-2.5 py-2">
            <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[9px] font-extrabold text-g2">
              <span className="font-mono text-ink">{group.chamberId ?? 'CHAMBER 미제공'}</span>
              {group.history?.step_id && <span>{group.history.step_id}</span>}
              {group.history?.recipe_id && <span>{group.history.recipe_id}</span>}
            </div>
            <div className="mt-1.5 flex flex-col gap-1.5">
              {group.alarms.map((alarm, index) => (
                <div key={alarmIdentity(alarm, index)} className="rounded-md bg-soft px-2 py-1.5 text-[10px] text-g1">
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <span className="font-extrabold text-red-600">{alarm.source} {alarm.judgement}</span>
                    <span className="font-mono font-bold">{alarm.sensor_id}</span>
                    <span>{alarm.rule_id ?? '-'}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-2 text-[9px] text-g2">
                    <span>{formatOccurredAt(alarm.occurred_at)}</span>
                    <span className="font-mono">{alarm.alarm_id ?? 'ID 미제공'}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

const waferDetailItems = (node) => {
  const properties = node.properties ?? {}
  const allowed = new Set(PUBLIC_NODE_PROPERTIES.Wafer)
  const publicHistory = (history) => Object.fromEntries(
    Object.entries(history ?? {}).filter(([key]) => key === 'chamber_id' || allowed.has(key)),
  )
  const histories = Array.isArray(properties.process_history) && properties.process_history.length > 0
    ? properties.process_history.map(publicHistory)
    : [publicHistory({
      chamber_id: properties.alarm_focus_chamber_id?.replace(/^Chamber:/, ''),
      step_id: properties.step_id,
      recipe_id: properties.recipe_id,
      track_in_at: properties.track_in_at,
      track_out_at: properties.track_out_at,
      chamber_wafer_cum: properties.chamber_wafer_cum,
    })]
  return {
    waferNo: properties.wafer_no,
    histories: histories.map((history) => [
      ['CHAMBER', history.chamber_id],
      ['STEP', history.step_id],
      ['RECIPE', history.recipe_id],
      ['TRACK IN', history.track_in_at ? formatOccurredAt(history.track_in_at) : null],
      ['TRACK OUT', history.track_out_at ? formatOccurredAt(history.track_out_at) : null],
      ['CHAMBER SEQUENCE', history.chamber_wafer_cum],
    ].filter(([, value]) => value != null && value !== '')).filter((history) => history.length > 0),
  }
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
  const isWaferNode = node.label === 'Wafer'
  const displayDetails = isWaferNode ? waferDetailItems(node) : details.map(({ key, value }) => [key, value])
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
          <span className="rounded-full bg-soft px-2 py-1 text-g1">표시 관계 {relationCount}건</span>
        </div>
      </div>
      <div className="border-t border-cell-line pt-3">
        <div className="mb-2 text-[10px] font-bold text-faint">{isWaferNode ? 'PROCESS HISTORY' : '공개 속성'}</div>
        {isWaferNode ? (
          <div className="flex flex-col gap-2">
            {displayDetails.waferNo != null && displayDetails.waferNo !== '' && (
              <div className="w-fit min-w-[150px] rounded-lg bg-soft px-3 py-2">
                <div className="text-[9.5px] font-bold text-g2">WAFER NO.</div>
                <div className="mt-0.5 break-all font-mono text-[11px] font-semibold text-ink">{displayDetails.waferNo}</div>
              </div>
            )}
            {displayDetails.histories.map((history, index) => (
              <div key={`${history.find(([key]) => key === 'CHAMBER')?.[1] ?? 'history'}-${index}`} className="rounded-lg border border-cell-line bg-white px-3 py-2.5">
                <div className="mb-2 text-[9.5px] font-extrabold text-faint">처리 이력 {index + 1}</div>
                <div className="flex flex-wrap gap-2">
                  {history.map(([key, value]) => (
                    <div key={key} className="min-w-[118px] rounded-md bg-soft px-2.5 py-2">
                      <div className="text-[9px] font-bold text-g2">{key}</div>
                      <div className="mt-0.5 break-all font-mono text-[10.5px] font-semibold text-ink">{value}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : displayDetails.length ? (
          <div className="flex flex-wrap gap-2">
            {displayDetails.map(([key, value]) => (
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
        {isWaferNode ? (
          <WaferAlarmContext node={node} state={alarmState} onRetry={onRetryAlarms} />
        ) : (
          <NodeOperationalStatus state={alarmState} onRetry={onRetryAlarms} />
        )}
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

function OntologyOverview({ graph, selectedNodeId, onSelectNode, isOpen, onToggle }) {
  const lanes = useMemo(() => buildOntologyOverviewLanes([graph]), [graph])
  if (lanes.length === 0) return null
  return (
    <Card className="p-4" data-testid="ontology-overview">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-[13px] font-extrabold text-ink">전체 구조 안내</div>
          <div className="mt-1 text-[10.5px] text-g2">
            두 모델의 실제 관계 흐름을 비교하고 챔버를 선택해 아래 그래프를 탐색합니다.
          </div>
        </div>
        <button type="button" onClick={onToggle} aria-expanded={isOpen}
          aria-label={`전체 구조 안내 ${isOpen ? '접기' : '펼치기'}`}
          className="grid h-7 w-7 place-items-center rounded-md text-[16px] font-extrabold text-g2 transition hover:bg-soft hover:text-ink">
          <span aria-hidden="true">{isOpen ? '△' : '▽'}</span>
        </button>
      </div>
      {isOpen && <div className="mt-3 overflow-x-auto pb-1">
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
      </div>}
    </Card>
  )
}

function OntologyPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const focus = useMemo(() => controlledFocus(searchParams), [searchParams])
  const [selectedChamber, setSelectedChamber] = useState(
    focus.phase === 'ready' ? focus.chamberId : '',
  )
  const [selectedLot, setSelectedLot] = useState('')
  const [selectedWafer, setSelectedWafer] = useState('')
  const [browseMode, setBrowseMode] = useState('structure')
  const [isOverviewOpen, setIsOverviewOpen] = useState(true)
  const [attempt, setAttempt] = useState(0)
  const graphResponseCache = useRef(new Map())
  const [state, setState] = useState({ status: 'loading', graph: null, partial: false })
  const [selectedNode, setSelectedNode] = useState(null)
  const [alarmAttempt, setAlarmAttempt] = useState(0)
  const [alarmState, setAlarmState] = useState({ requestKey: null, status: 'idle', summary: null, basis: null })
  const [waferAlarmState, setWaferAlarmState] = useState({ requestKey: null, status: 'idle', alarms: [] })
  const explicitChamber = focus.phase === 'ready' ? focus.chamberId : selectedChamber
  const activeBrowseMode = focus.phase === 'ready' ? 'chamber' : browseMode
  const requestChamber = explicitChamber || DEFAULT_GRAPH_CHAMBER
  const rawGraph = useMemo(() => {
    if (!state.graph) return null
    if (!explicitChamber) return state.graph
    const rootNodeId = `Chamber:${explicitChamber}`
    return state.graph.nodes.some((node) => node.id === rootNodeId)
      ? { ...state.graph, root_node_id: rootNodeId }
      : state.graph
  }, [explicitChamber, state.graph])
  const lotOptions = useMemo(
    () => lotOptionsForChamber(rawGraph, explicitChamber),
    [rawGraph, explicitChamber],
  )
  // LOT / WAFER 탭은 빈 전체 구조를 다시 보여 주지 않는다. 생산 이력이 준비되면
  // selector 최상단 LOT를 유효 선택값으로 사용해 실제 공정 경로 projection을 바로 연다.
  const activeLotId = selectedLot || (activeBrowseMode === 'lot' ? lotOptions[0]?.id ?? '' : '')
  const baseGraph = useMemo(
    () => buildLotContextGraph(rawGraph, activeLotId, explicitChamber, selectedWafer),
    [rawGraph, activeLotId, explicitChamber, selectedWafer],
  )
  const waferOptions = useMemo(
    () => waferOptionsForLot(rawGraph, activeLotId, explicitChamber),
    [rawGraph, activeLotId, explicitChamber],
  )
  const selectedLotNode = useMemo(
    () => baseGraph?.nodes.find((node) => node.label === 'Lot' && node.business_id === activeLotId) ?? null,
    [baseGraph, activeLotId],
  )
  const selectedWaferNode = useMemo(
    () => baseGraph?.nodes.find((node) => node.id === selectedWafer && node.label === 'Wafer') ?? null,
    [baseGraph, selectedWafer],
  )
  const selectedChamberNode = useMemo(
    () => baseGraph?.nodes.find((node) => node.id === `Chamber:${explicitChamber}`) ?? null,
    [baseGraph, explicitChamber],
  )
  // 상단 Chamber/LOT 선택은 조회 context일 뿐 그래프 node를 클릭한 상태가 아니다.
  // 상단 WAFER selector는 표시 범위를 정하는 필터다. 그래프 click과 달리
  // node/edge를 강조하거나 나머지를 흐리게 하지 않지만, 해당 Wafer의 ALARM_ON
  // 관계는 즉시 표시해야 한다.
  const visualSelectedNode = focus.phase === 'ready' ? null : selectedNode
  const panelNode = focus.phase === 'ready' ? null : selectedNode ?? selectedWaferNode ?? selectedLotNode ?? selectedChamberNode
  const alarmRequestKey = panelNode ? `${panelNode.id}:${alarmAttempt}` : null
  const activeAlarmState = alarmState.requestKey === alarmRequestKey ? alarmState : null
  const waferAlarmNode = selectedWaferNode
    ?? (visualSelectedNode?.label === 'Wafer' ? visualSelectedNode : null)
  const waferAlarmRequestKey = waferAlarmNode ? `${waferAlarmNode.id}:${alarmAttempt}` : null
  const activeWaferAlarmState = waferAlarmState.requestKey === waferAlarmRequestKey ? waferAlarmState : null
  const graph = useMemo(
    () => attachWaferAlarmContext(
      annotateWaferAlarmHints(baseGraph, [
        ...(activeAlarmState?.incident ? activeAlarmState.alarms : []),
        ...(activeWaferAlarmState?.wafer ? activeWaferAlarmState.alarms : []),
      ]),
      waferAlarmNode?.id,
      activeWaferAlarmState?.alarms ?? [],
    ),
    [activeAlarmState, activeWaferAlarmState, baseGraph, waferAlarmNode],
  )

  useEffect(() => {
    graphResponseCache.current.clear()
  }, [attempt])

  useEffect(() => {
    if (focus.phase === 'invalid') return undefined
    let active = true
    const loadAllChambers = activeBrowseMode === 'structure' || activeBrowseMode === 'lot'
    const orderedChambers = loadAllChambers
      ? [requestChamber, ...GRAPH_CHAMBERS.filter((chamberId) => chamberId !== requestChamber)]
      : [requestChamber]
    const includeProductionContext = activeBrowseMode === 'lot' || Boolean(explicitChamber)
    Promise.allSettled(orderedChambers.map((chamberId) => {
      const cacheKey = `${chamberId}:${includeProductionContext}`
      const cached = graphResponseCache.current.get(cacheKey)
      if (cached) return cached
      const request = getChamberRelationsCore(
        chamberId,
        { include_production_context: includeProductionContext },
      )
      graphResponseCache.current.set(cacheKey, request)
      request.catch(() => {
        if (graphResponseCache.current.get(cacheKey) === request) {
          graphResponseCache.current.delete(cacheKey)
        }
      })
      return request
    })).then((results) => {
      if (!active) return
      const requestedResult = results[0]
      if (requestedResult.status !== 'fulfilled') {
        setState({ status: 'error', graph: null, partial: false })
        return
      }
      const responses = results
        .filter((result) => result.status === 'fulfilled')
        .map((result) => result.value)
      const merged = mergeOntologyGraphs(responses, `Chamber:${requestChamber}`)
      setState({
        status: hasDisplayableRelationships(merged) ? 'success' : 'empty',
        graph: merged,
        partial: responses.length !== orderedChambers.length || Boolean(merged.production_context?.truncated),
      })
    })
    return () => {
      active = false
    }
  }, [activeBrowseMode, attempt, explicitChamber, focus.phase, requestChamber])

  useEffect(() => {
    const scope = ontologyAlarmScope(baseGraph, panelNode)
    if (!panelNode || !scope || panelNode.id === waferAlarmNode?.id) return undefined
    let active = true
    const requestKey = `${panelNode.id}:${alarmAttempt}`
    Promise.allSettled(scope.requests.map((params) => getAllAlarms(params))).then((results) => {
      if (!active) return
      const responses = results.filter((result) => result.status === 'fulfilled').map((result) => result.value)
      if (responses.length === 0) {
        setAlarmState({ requestKey, status: 'error', summary: null, basis: scope.basis, incident: Boolean(scope.incident), lot: Boolean(scope.lot), wafer: Boolean(scope.wafer) })
        return
      }
      const alarms = alarmsForOntologyScope(scope, responses)
      setAlarmState({
        requestKey,
        status: 'success',
        summary: summarizeOntologyAlarms(alarms),
        basis: scope.basis,
        incident: Boolean(scope.incident),
        lot: Boolean(scope.lot),
        wafer: Boolean(scope.wafer),
        alarms,
        mock: responses.some((response) => response.mock),
        partial: results.length !== responses.length || responses.some((response) => response.partial),
      })
    })
    return () => {
      active = false
    }
  }, [panelNode, alarmAttempt, baseGraph, waferAlarmNode])

  useEffect(() => {
    const scope = ontologyAlarmScope(baseGraph, waferAlarmNode)
    if (!waferAlarmNode || !scope) return undefined
    let active = true
    const requestKey = `${waferAlarmNode.id}:${alarmAttempt}`
    Promise.allSettled(scope.requests.map((params) => getAllAlarms(params))).then((results) => {
      if (!active) return
      const responses = results.filter((result) => result.status === 'fulfilled').map((result) => result.value)
      if (responses.length === 0) {
        setWaferAlarmState({ requestKey, status: 'error', alarms: [], basis: scope.basis, wafer: true })
        return
      }
      const alarms = alarmsForOntologyScope(scope, responses)
      setWaferAlarmState({
        requestKey, status: 'success', alarms, basis: scope.basis, wafer: true,
        partial: results.length !== responses.length || responses.some((response) => response.partial),
      })
    })
    return () => {
      active = false
    }
  }, [alarmAttempt, baseGraph, waferAlarmNode])

  const selectedAlarmScope = ontologyAlarmScope(baseGraph, panelNode)
  const displayedAlarmState = panelNode?.id === waferAlarmNode?.id
    ? activeWaferAlarmState ?? { status: 'loading', basis: ontologyAlarmScope(baseGraph, waferAlarmNode)?.basis, wafer: true }
    : !panelNode
    ? { status: 'idle' }
    : !selectedAlarmScope
      ? { status: 'unsupported' }
      : activeAlarmState
        ? activeAlarmState
        : { status: 'loading', basis: selectedAlarmScope.basis, incident: Boolean(selectedAlarmScope.incident), lot: Boolean(selectedAlarmScope.lot), wafer: Boolean(selectedAlarmScope.wafer) }

  const resolvedFocus = useMemo(() => {
    if (focus.phase !== 'ready' || state.status !== 'success' || !graph) return focus
    return resolveOntologyFocus(graph, focus)
  }, [focus, graph, state.status])
  const focusedRelationIds = useMemo(
    () => new Set(resolvedFocus.phase === 'found' && resolvedFocus.kind === 'relation' ? [resolvedFocus.relation.id] : []),
    [resolvedFocus],
  )
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
  const displayedNode = panelNode && graph?.nodes.some((node) => node.id === panelNode.id)
    ? panelNode
    : focus.phase === 'ready'
      ? graph?.nodes.find((node) => node.id === graph.root_node_id) ?? null
      : null
  const graphScopeLabel = selectedWaferNode
    ? `Wafer ${selectedWaferNode.business_id}`
    : activeLotId
      ? `LOT ${activeLotId}`
      : explicitChamber ? null : '전체 온톨로지'
  // Selector는 화면 projection 자체를 바꾼다. React Flow가 이전 viewport/node 내부 상태를
  // 재사용하지 않도록 context 전환마다 Canvas를 새 graph로 다시 초기화한다.
  const graphViewKey = `${activeBrowseMode}:${explicitChamber}:${activeLotId}:${selectedWafer}`

  const changeChamber = (chamberId) => {
    setSelectedChamber(chamberId)
    setSelectedLot('')
    setSelectedWafer('')
    setSelectedNode(null)
    setSearchParams({}, { replace: true })
  }

  const selectChamber = (event) => changeChamber(event.target.value)
  const selectLot = (event) => {
    setSelectedLot(event.target.value)
    setSelectedWafer('')
    setSelectedNode(null)
  }
  const selectWafer = (event) => {
    // Wafer는 전체 공정 경로를 보는 기준이다. Chamber 기준과 함께 유지하지 않는다.
    if (event.target.value) {
      setSelectedChamber('')
      setSearchParams({}, { replace: true })
    }
    setSelectedWafer(event.target.value)
    setSelectedNode(null)
  }

  const selectBrowseMode = (mode) => {
    setBrowseMode(mode)
    setSelectedNode(null)
    if (mode === 'structure') {
      setSelectedChamber('')
      setSelectedLot('')
      setSelectedWafer('')
    } else if (mode === 'chamber') {
      setSelectedLot('')
      setSelectedWafer('')
    } else if (mode === 'lot') {
      setSelectedChamber('')
    }
    setSearchParams({}, { replace: true })
  }

  const selectGraphNode = (node) => {
    setSelectedNode(node)
    if (node && focus.phase === 'ready') setSearchParams({}, { replace: true })
  }

  const selectOverviewNode = (node) => {
    // 전체 구조 안내에서는 node 종류와 무관하게 상세 조회 대상만 바꾼다.
    // 상단 Chamber/LOT selector는 사용자가 직접 선택할 때만 바뀐다.
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
        <div className="flex flex-wrap items-center justify-end gap-3">
          <div className="flex rounded-lg border border-field-line bg-soft p-1" aria-label="온톨로지 탐색 관점">
            {[
              ['structure', '전체 구조'], ['chamber', '챔버'], ['lot', 'LOT / WAFER'],
            ].map(([mode, label]) => (
              <button key={mode} type="button" onClick={() => selectBrowseMode(mode)}
                className={`rounded-md px-2.5 py-1.5 text-[11px] font-extrabold transition ${activeBrowseMode === mode ? 'bg-white text-blue shadow-sm' : 'text-g2 hover:text-ink'}`}>
                {label}
              </button>
            ))}
          </div>
          {activeBrowseMode === 'chamber' && <>
          <label className="flex items-center gap-2 text-[11px] font-bold text-g2">
            CHAMBER
            <select value={explicitChamber} onChange={selectChamber}
              className="h-9 min-w-[180px] rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] font-bold text-ink"
              aria-label="온톨로지 챔버 선택">
              <option value="">전체 구조</option>
              {GRAPH_CHAMBERS.map((chamberId) => <option key={chamberId} value={chamberId}>{chamberId}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 text-[11px] font-bold text-g2">
            LOT
            <select value={activeLotId} onChange={selectLot}
              className="h-9 min-w-[150px] rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] font-bold text-ink"
              aria-label="온톨로지 Lot 선택">
              <option value="">LOT 선택 (선택 사항)</option>
              {lotOptions.map((lot) => <option key={lot.id} value={lot.id}>{lot.label}</option>)}
            </select>
          </label>
          </>}
          {activeBrowseMode === 'lot' && <>
          <label className="flex items-center gap-2 text-[11px] font-bold text-g2">
            LOT
            <select value={activeLotId} onChange={selectLot} disabled={lotOptions.length === 0}
              className="h-9 min-w-[150px] rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] font-bold text-ink disabled:cursor-not-allowed disabled:bg-soft disabled:text-faint"
              aria-label="온톨로지 Lot 선택">
              {lotOptions.length === 0
                ? <option value="">생산 이력 없음</option>
                : lotOptions.map((lot) => <option key={lot.id} value={lot.id}>{lot.label}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 text-[11px] font-bold text-g2">
            WAFER
            <select value={selectedWafer} onChange={selectWafer} disabled={!activeLotId || Boolean(explicitChamber)}
              className="h-9 min-w-[160px] rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] font-bold text-ink disabled:cursor-not-allowed disabled:bg-soft disabled:text-faint"
              aria-label="온톨로지 Wafer 선택">
              <option value="">{!activeLotId ? 'LOT를 먼저 선택' : explicitChamber ? 'CHAMBER 선택 해제 필요' : 'WAFER 선택'}</option>
              {waferOptions.map((wafer) => <option key={wafer.id} value={wafer.id}>{wafer.label}</option>)}
            </select>
          </label>
          </>}
        </div>
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
            setState({ status: 'loading', graph: null, partial: false })
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
              일부 보조 챔버 관계를 불러오지 못했거나 생산 이력 반환 상한에 도달해, 확인 가능한 관계만 표시합니다.
            </div>
          )}
          {activeBrowseMode === 'structure' && !explicitChamber && !activeLotId && (
            <OntologyOverview graph={graph} selectedNodeId={visualSelectedNode?.id ?? null} onSelectNode={selectOverviewNode}
              isOpen={isOverviewOpen} onToggle={() => setIsOverviewOpen((open) => !open)} />
          )}
          <div className="grid items-start gap-4 md:grid-cols-[minmax(0,1fr)_280px] lg:grid-cols-[minmax(0,1fr)_320px] xl:grid-cols-[minmax(0,1fr)_360px]">
            <OntologyGraphCanvas key={graphViewKey} graph={graph} focusedRelationIds={focusedRelationIds}
              impactNodeIds={focusedImpactNodeIds} checkRequiredNodeIds={checkRequiredNodeIds}
              selectedNodeId={visualSelectedNode?.id ?? null} onSelectNode={selectGraphNode} viewport="page"
              scopeNodeId={selectedChamberNode?.id ?? null}
              incidentNodeId={selectedWafer ? null : selectedLotNode?.id ?? null}
              waferSelectionNodeId={selectedWafer || null}
              emphasizeRoot={focus.phase === 'ready'} chamberOnly={Boolean(explicitChamber && !activeLotId)} />
            <div className="flex flex-col gap-3 md:sticky md:top-4">
              <GraphSummary graph={graph} compact scopeLabel={graphScopeLabel} />
              <NodeDetail graph={graph} node={displayedNode} isSelected={Boolean(selectedNode)}
                alarmState={displayedAlarmState} onRetryAlarms={() => setAlarmAttempt((value) => value + 1)} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default OntologyPage
