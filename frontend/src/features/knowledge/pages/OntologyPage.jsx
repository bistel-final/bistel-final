import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getChamberRelationsCore } from '../../../shared/api/knowledge.js'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'
import { parseOntologyFocus, resolveOntologyFocus } from '../ontology-focus-state.js'

// 온톨로지 — 라이트 시안 5번. Neo4j Browser 임베드 (연결/해제 토글)
// 헤더 툴바: `graph fdc` + Cypher 예시(모노 dim) + URL 입력(모노) + [임베드 연결]/[연결 해제]
const CYPHER_EXAMPLE = 'MATCH (c:Chamber)-[r]->(n) RETURN c, r, n LIMIT 50'

function OntologyPage() {
  const [searchParams] = useSearchParams()
  const [url, setUrl] = useState('')
  const [embedUrl, setEmbedUrl] = useState(null)
  const requestedFocus = useMemo(() => parseOntologyFocus(searchParams), [searchParams])
  const focusKey = requestedFocus.phase === 'ready'
    ? `${requestedFocus.chamberId}:${requestedFocus.relationId}:${requestedFocus.graphRevision}`
    : null
  const [resolved, setResolved] = useState({ key: null, focus: null })
  const focus = requestedFocus.phase === 'ready'
    ? resolved.key === focusKey
      ? resolved.focus
      : { ...requestedFocus, phase: 'loading' }
    : requestedFocus

  const connected = Boolean(embedUrl)

  useEffect(() => {
    if (requestedFocus.phase !== 'ready') return undefined
    let active = true
    getChamberRelationsCore(requestedFocus.chamberId).then(
      (graph) => {
        if (active) setResolved({ key: focusKey, focus: resolveOntologyFocus(graph, requestedFocus) })
      },
      () => {
        if (active) {
          setResolved({
            key: focusKey,
            focus: {
              ...requestedFocus,
              phase: 'error',
              message: '연결된 그래프 근거를 불러오지 못했습니다.',
            },
          })
        }
      },
    )
    return () => {
      active = false
    }
  }, [focusKey, requestedFocus])

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[20px] font-extrabold text-ink">온톨로지</div>
        <div className="text-[11.5px] text-g2">설비 · 챔버 · 파라미터 · Fault 지식 그래프</div>
      </div>

      <Card className="flex flex-wrap items-center gap-3 px-4 py-3">
        <span className="rounded-md bg-tint-navy px-2.5 py-1 font-mono text-[11.5px] font-bold text-navy">graph fdc</span>
        <span className="font-mono text-[11px] text-faint">{CYPHER_EXAMPLE}</span>
        <span className="ml-auto flex items-center gap-2">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={connected}
            placeholder="http://<neo4j-host>:7474/browser/"
            className="h-9 w-[340px] rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] text-ink placeholder:text-faint disabled:bg-soft disabled:text-g2"
          />
          {connected ? (
            <Button variant="outline-red" onClick={() => setEmbedUrl(null)}>
              연결 해제
            </Button>
          ) : (
            <Button onClick={() => url.trim() && setEmbedUrl(url.trim())} disabled={!url.trim()}>
              임베드 연결
            </Button>
          )}
        </span>
      </Card>

      {focus.phase !== 'none' && (
        <Card className="mt-4 px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-[13px] font-extrabold text-ink">Agent 그래프 근거</div>
              {focus.phase === 'loading' && <div className="mt-1 text-[12px] text-g2">연결 관계를 복원하는 중…</div>}
              {focus.phase === 'found' && (
                <>
                  <div className="mt-2 flex flex-wrap items-center gap-2 font-mono text-[12px] font-bold text-blue">
                    <span>{focus.source?.business_id ?? focus.relation.source}</span>
                    <span>—[{focus.relation.type}]→</span>
                    <span>{focus.target?.business_id ?? focus.relation.target}</span>
                  </div>
                  <div className="mt-2 font-mono text-[10.5px] text-g2">
                    {focus.relationId} · {focus.chamberId} · rev {focus.graphRevision}
                  </div>
                  <div className="mt-3 rounded-lg border border-line bg-soft px-3 py-2 font-mono text-[11px] text-g1">
                    MATCH (a)-[r]→(b) WHERE r.relation_id = '{focus.relationId}' RETURN a, r, b
                  </div>
                </>
              )}
              {['invalid', 'error', 'not-found', 'revision-mismatch'].includes(focus.phase) && (
                <div className="mt-1 text-[12px] font-bold text-red">
                  {focus.message ??
                    (focus.phase === 'revision-mismatch'
                      ? `그래프 버전이 달라 근거 관계를 복원할 수 없습니다. 현재 rev ${focus.actualRevision}`
                      : '해당 그래프 관계를 찾을 수 없습니다.')}
                </div>
              )}
            </div>
            {focus.phase === 'found' && (
              <span className="rounded-full border border-tint-green-line bg-state-green-bg px-3 py-1 text-[11px] font-bold text-green-dark">
                관계 복원 완료
              </span>
            )}
          </div>
        </Card>
      )}

      <div className="mt-4">
        {connected ? (
          <iframe
            title="Neo4j Browser"
            src={embedUrl}
            className="w-full rounded-[10px] border border-line bg-white"
            style={{ height: 'calc(100vh - 160px)' }}
          />
        ) : (
          <div
            className="flex flex-col items-center justify-center gap-2.5 rounded-[10px] border-[1.5px] border-dashed border-dash-line bg-white text-center"
            style={{ height: 'calc(100vh - 160px)' }}
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-tint-blue font-mono text-[18px] font-extrabold text-blue">
              ⧉
            </div>
            <div className="text-[14px] font-extrabold text-ink">Neo4j Browser 임베드가 연결되지 않았습니다</div>
            <div className="max-w-[420px] text-[12px] leading-[1.7] text-g1">
              위 입력창에 팀 공용 Neo4j Browser 주소를 넣고 <span className="font-bold text-ink">임베드 연결</span>을 누르면
              이 영역에 지식 그래프 탐색 화면이 표시됩니다.
              <br />
              <span className="font-mono text-[11px] text-faint">예시 질의: {CYPHER_EXAMPLE}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default OntologyPage
