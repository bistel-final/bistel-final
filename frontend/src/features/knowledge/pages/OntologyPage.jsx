import { useState } from 'react'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'

// 온톨로지 — 라이트 시안 5번. Neo4j Browser 임베드 (연결/해제 토글)
// 헤더 툴바: `graph fdc` + Cypher 예시(모노 dim) + URL 입력(모노) + [임베드 연결]/[연결 해제]
const CYPHER_EXAMPLE = 'MATCH (c:Chamber)-[r]->(n) RETURN c, r, n LIMIT 50'

function OntologyPage() {
  const [url, setUrl] = useState('')
  const [embedUrl, setEmbedUrl] = useState(null)

  const connected = Boolean(embedUrl)

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
