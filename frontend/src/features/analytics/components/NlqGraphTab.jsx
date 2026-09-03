import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { getChamberRelationsCore } from '../../../shared/api/knowledge.js'
import OntologyGraphCanvas from '../../../shared/components/ontology/OntologyGraphCanvas.jsx'
import { mergeOntologyGraphs } from '../../../shared/graph/ontology-graph.js'
import { GRAPH_CHAMBERS } from '../../../shared/graph/ontology-chambers.js'

// 교차확인 그래프 탭 — SQL 답을 재확인한 온톨로지 부분 그래프를 "근거 한 장"으로 보여준다.
// 데이터는 B파트 공개 API GET /relations/chambers/{id}(실 Neo4j), 그리기는 공용 OntologyGraphCanvas.
// 답에 해당하는 노드·관계만 남긴 부분 그래프를 넘기고(챔버 개수 질문 → 설비 + 그 챔버들),
// 탐색 도구(확대·축소·클릭)는 숨기고 고정한다 — 탐색은 온톨로지 화면의 몫이라 링크로 넘긴다.
// 공용 컴포넌트는 건드리지 않고 이 탭 안에서만 CSS 로 숨긴다.

const EQP_RE = /\bEQP\d{2}(?:-PM\d)?\b/g

// 어느 관계를 남길지 — Cypher·SQL 이 언급한 대상으로 결정한다 (결정론, LLM 없음)
const FOCUS_BY_TEXT = [
  [/chamber/i, 'PART_OF'],
  [/parameter|\bparam\b/i, 'MEASURED_ON'],
  [/processstep|process_step|\bstep\b/i, 'PERFORMS'],
  [/equipmentmodel|model_code|\bmodel\b/i, 'OF_MODEL'],
  [/\barea\b/i, 'IN_AREA'],
]

// 식별자 → 조회할 챔버 목록. 설비(EQP04)면 그 설비의 챔버 전부, 챔버면 그 챔버.
export function scopeChambers(text) {
  const ids = new Set(String(text ?? '').match(EQP_RE) ?? [])
  const chambers = new Set()
  for (const id of ids) {
    if (id.includes('-PM')) {
      if (GRAPH_CHAMBERS.includes(id)) chambers.add(id)
    } else {
      GRAPH_CHAMBERS.filter((c) => c.startsWith(`${id}-`)).forEach((c) => chambers.add(c))
    }
  }
  return [...chambers]
}

// 그래프 안에서 실제 루트 노드를 찾는다 — ID 문자열을 조립하지 않고 라벨·business_id 로 찾아 형식 차이에 안전하게
function resolveRoot(graph, text) {
  if (!graph) return null
  const ids = String(text ?? '').match(EQP_RE) ?? []
  const eqp = ids.find((id) => !id.includes('-PM'))
  const ch = ids.find((id) => id.includes('-PM'))
  const byBiz = (label, biz) => graph.nodes.find((n) => n.label === label && n.business_id === biz)?.id ?? null
  return (eqp && byBiz('Equipment', eqp)) || (ch && byBiz('Chamber', ch)) || graph.root_node_id || null
}

function focusTypes(text) {
  return new Set(FOCUS_BY_TEXT.filter(([re]) => re.test(text ?? '')).map(([, t]) => t))
}

// 답에 해당하는 부분만 남긴 그래프 — 루트에 붙은 대상 유형 관계와 그 양 끝 노드.
// 유형을 모르면 루트의 직접 관계 전부.
function subgraphOf(graph, rootId, types) {
  if (!graph) return null
  const touchesRoot = (r) => !rootId || r.source === rootId || r.target === rootId
  let rels = graph.relationships.filter((r) => touchesRoot(r) && (types.size === 0 || types.has(r.type)))
  if (rels.length === 0) rels = graph.relationships.filter(touchesRoot)
  const keep = new Set(rels.flatMap((r) => [r.source, r.target]))
  if (rootId) keep.add(rootId)
  const nodes = graph.nodes.filter((n) => keep.has(n.id))
  // 그리기용 루트는 반드시 챔버 — 공용 레이아웃은 루트를 챔버 열에서만 배치해서, 설비를 루트로 주면 그 노드가 빠진다.
  // 관계를 고르는 기준(rootId)과 그리기용 루트를 분리한다.
  const drawRoot = nodes.find((n) => n.label === 'Chamber')?.id ?? nodes[0]?.id ?? graph.root_node_id
  return { ...graph, root_node_id: drawRoot, nodes, relationships: rels }
}

// 온톨로지 화면 딥링크 — 보이는 관계 중 첫 것으로 포커스(chamber_id·relation_id·graph_revision 세트), 없으면 화면만
function ontologyHref(graph, chambers) {
  if (!graph || graph.relationships.length === 0 || chambers.length === 0 || !graph.graph_revision) return '/ontology'
  const rel = graph.relationships[0]
  const q = new URLSearchParams({
    chamber_id: chambers[0],
    relation_id: rel.canonical_id ?? rel.id,
    graph_revision: graph.graph_revision,
  })
  return `/ontology?${q.toString()}`
}

const EMPTY = new Set()

function NlqGraphTab({ def }) {
  const scopeText = `${def?.cross_check?.cypher ?? ''}\n${def?.generated_sql ?? ''}`
  const chambers = useMemo(() => scopeChambers(scopeText), [scopeText])
  const [state, setState] = useState({ phase: 'loading', graph: null })

  useEffect(() => {
    if (chambers.length === 0) {
      setState({ phase: 'empty', graph: null })
      return undefined
    }
    let cancelled = false
    setState({ phase: 'loading', graph: null })
    Promise.allSettled(chambers.map((c) => getChamberRelationsCore(c)))
      .then((results) => {
        if (cancelled) return
        const graphs = results.filter((r) => r.status === 'fulfilled').map((r) => r.value)
        const merged = mergeOntologyGraphs(graphs, null)
        setState(merged ? { phase: 'ready', graph: merged } : { phase: 'error', graph: null })
      })
      .catch(() => {
        if (!cancelled) setState({ phase: 'error', graph: null })
      })
    return () => {
      cancelled = true
    }
  }, [chambers])

  const rootId = useMemo(() => resolveRoot(state.graph, scopeText), [state.graph, scopeText])
  const shown = useMemo(() => subgraphOf(state.graph, rootId, focusTypes(scopeText)), [state.graph, rootId, scopeText])

  return (
    <div className="flex flex-col gap-3">
      {state.phase === 'loading' && (
        <div className="flex h-[400px] items-center justify-center rounded-[10px] border border-line bg-soft text-[12.5px] text-g2">
          설비 구성 정보를 불러오는 중…
        </div>
      )}
      {state.phase === 'empty' && (
        <div className="rounded-[10px] border border-line bg-soft px-4 py-3.5 text-[12.5px] text-g1">
          이 질의는 특정 설비·챔버를 가리키지 않아 그릴 구성도가 없습니다.
        </div>
      )}
      {state.phase === 'error' && (
        <div className="rounded-[10px] border border-line bg-soft px-4 py-3.5 text-[12.5px] text-g1">
          설비 구성 정보를 불러오지 못했습니다.
        </div>
      )}
      {state.phase === 'ready' && shown && (
        // 고정 뷰 — 컨트롤·워터마크 숨김, 마우스 조작 차단. 공용 컴포넌트 무변경: data 속성으로 범위를 한정한 style 한 조각
        <div data-nlq-graph="" className="pointer-events-none select-none">
          <style>{`[data-nlq-graph] .react-flow__controls,[data-nlq-graph] .react-flow__attribution,[data-nlq-graph] .react-flow__panel{display:none !important}`}</style>
          <OntologyGraphCanvas graph={shown} focusedRelationIds={EMPTY} viewport="compact" />
        </div>
      )}
      {state.phase === 'ready' && (
        <div className="flex justify-end">
          <Link to={ontologyHref(shown, chambers)} className="text-[12.5px] font-semibold text-blue hover:text-blue-hover">
            설비 구성 전체 보기 →
          </Link>
        </div>
      )}
    </div>
  )
}

export default NlqGraphTab
