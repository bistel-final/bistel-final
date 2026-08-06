import { useCallback, useEffect, useState } from 'react'
import { getChamberRelations, searchDocuments } from '../../../shared/api/knowledge.js'
import { DOC_FILTERS, DOC_CHIPS } from '../mock/documents.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'

const ST_COLOR = {
  ALARM: { clr: '#D97706', bg: '#FEF3C7' },
  NORMAL: { clr: '#16A34A', bg: '#DCFCE7' },
  CRITICAL: { clr: '#DC2626', bg: '#FEE2E2' },
}

function ChamberNode({ node, selected, onClick }) {
  const st = ST_COLOR[node.status]
  return (
    <div
      onClick={onClick}
      className="relative cursor-pointer rounded-[10px] border-[2.5px] bg-white px-4 py-[9px] text-center transition-transform duration-150 hover:-translate-y-0.5"
      style={{
        borderColor: st.clr,
        boxShadow: selected ? `0 0 0 4px ${st.bg}` : '0 1px 3px rgba(15,42,92,.08)',
      }}
    >
      <div className="font-mono text-sm font-extrabold text-navy">{node.name}</div>
      <div className="mt-0.5 text-[11px] font-extrabold" style={{ color: st.clr }}>
        {node.status}
      </div>
    </div>
  )
}

function KnowledgePage() {
  const [relations, setRelations] = useState(null)
  const [error, setError] = useState(null)
  const [selCh, setSelCh] = useState(null)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('전체')
  const [results, setResults] = useState([])

  const load = useCallback(() => {
    getChamberRelations('ALL')
      .then(setRelations)
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  // dc.html 동작: 질문이 예시 질의와 일치하면 즉시 결과 표시, 필터는 결과에 실시간 적용
  const search = (q, f) => {
    if (!q) {
      setResults([])
      return
    }
    searchDocuments({ query: q, model_code: f, top_k: 4 }).then((r) => setResults(r.results))
  }
  const changeQuery = (q) => {
    setQuery(q)
    search(q, filter)
  }
  const changeFilter = (f) => {
    setFilter(f)
    search(query, f)
  }

  if (error) return <ErrorState detail={error} onRetry={() => { setError(null); load() }} />
  if (!relations) return <LoadingState message="관계 데이터를 불러오는 중…" />

  const phoChambers = relations.chambers.filter((c) => c.group === 'pho')
  const etcChambers = relations.chambers.filter((c) => c.group === 'etc')
  const sel = relations.chambers.find((c) => c.name === selCh)
  const selSt = sel ? ST_COLOR[sel.status] : null

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
      <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">관계·문서 근거</div>
      <div className="grid grid-cols-[3fr_2fr] items-start gap-4">
        <div className="rounded-xl border border-line bg-white px-[22px] py-5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <div className="mb-3 text-base font-extrabold text-navy">장비 관계 그래프</div>
          <div className="relative h-[560px]">
            <div className="absolute left-[8%] right-[8%] top-3 h-[220px] rounded-[14px] border-2 border-[#BFDBFE] bg-[rgba(46,123,232,.03)]">
              <div className="absolute -top-3 left-[18px] bg-white px-2.5 text-[12.5px] font-extrabold tracking-[.5px] text-brand">
                PHOTO 공정
              </div>
              <div className="absolute left-1/2 top-[18px] -translate-x-1/2 rounded-[10px] bg-navy px-[22px] py-2.5 text-center shadow-[0_4px_10px_rgba(15,42,92,.25)]">
                <div className="font-mono text-[15px] font-extrabold text-white">PHO-01</div>
                <div className="font-mono text-[11.5px] font-bold text-sidebar-dim">PH-9000</div>
              </div>
              <div className="absolute bottom-6 left-0 right-0 flex justify-center gap-[120px]">
                {phoChambers.map((n) => (
                  <ChamberNode key={n.name} node={n} selected={selCh === n.name} onClick={() => setSelCh(n.name)} />
                ))}
              </div>
              <div className="absolute bottom-[52px] left-1/2 w-24 -translate-x-1/2 border-t-2 border-dashed border-line-input" />
            </div>
            <div className="absolute left-1/2 top-[236px] h-20 w-[3px] -translate-x-1/2 bg-brand">
              <div className="absolute -bottom-0.5 left-1/2 h-0 w-0 -translate-x-1/2 border-l-8 border-r-8 border-t-[12px] border-l-transparent border-r-transparent border-t-brand" />
              <div className="absolute left-3.5 top-1/2 -translate-y-1/2 whitespace-nowrap rounded-md border border-[#BFDBFE] bg-line-soft px-2.5 py-[3px] font-mono text-[11.5px] font-extrabold text-brand">
                UPSTREAM_OF
              </div>
            </div>
            <div className="absolute left-[8%] right-[8%] top-[322px] h-[220px] rounded-[14px] border-2 border-[#BFDBFE] bg-[rgba(53,181,200,.04)]">
              <div className="absolute -top-3 left-[18px] bg-white px-2.5 text-[12.5px] font-extrabold tracking-[.5px] text-[#0E7490]">
                ETCH 공정
              </div>
              <div className="absolute left-1/2 top-[18px] -translate-x-1/2 rounded-[10px] bg-navy px-[22px] py-2.5 text-center shadow-[0_4px_10px_rgba(15,42,92,.25)]">
                <div className="font-mono text-[15px] font-extrabold text-white">ETC-01</div>
                <div className="font-mono text-[11.5px] font-bold text-sidebar-dim">ET-7500</div>
              </div>
              <div className="absolute bottom-6 left-0 right-0 flex justify-center gap-[120px]">
                {etcChambers.map((n) => (
                  <ChamberNode key={n.name} node={n} selected={selCh === n.name} onClick={() => setSelCh(n.name)} />
                ))}
              </div>
              <div className="absolute bottom-[52px] left-1/2 w-24 -translate-x-1/2 border-t-2 border-dashed border-line-input" />
            </div>
          </div>
        </div>
        <div className="flex flex-col gap-3.5">
          {sel && (
            <div
              className="animate-[om-fadein_.25s] rounded-xl border border-t-4 bg-white px-[18px] py-4 shadow-[0_1px_3px_rgba(15,42,92,.05)]"
              style={{
                borderColor: sel.status === 'CRITICAL' ? '#FECACA' : '#E2E8F0',
                borderTopColor: selSt.clr,
              }}
            >
              <div className="flex items-center gap-2.5">
                <span className="font-mono text-base font-extrabold text-navy">{sel.name}</span>
                <span
                  className="ml-auto rounded-full px-2.5 py-1 text-xs font-extrabold"
                  style={{ background: selSt.bg, color: selSt.clr }}
                >
                  {sel.status}
                </span>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2.5">
                {[
                  ['알람', sel.total, '#0F2A5C'],
                  ['OOS', sel.oos, '#DC2626'],
                  ['OOC', sel.ooc, '#D97706'],
                ].map(([k, v, color]) => (
                  <div key={k} className="rounded-lg bg-page px-3 py-2 text-center">
                    <div className="text-[11px] font-bold text-slate-light">{k}</div>
                    <div className="font-mono text-[19px] font-extrabold" style={{ color }}>
                      {v}
                    </div>
                  </div>
                ))}
              </div>
              {sel.hold && (
                <div className="mt-2.5 inline-flex items-center gap-1.5 rounded-md bg-oos-soft px-2.5 py-[5px] text-xs font-extrabold text-oos">
                  <span className="h-[7px] w-[7px] animate-[om-pulse_1.4s_infinite] rounded-full bg-oos" />
                  EQP_HOLD 승인 대기
                </div>
              )}
            </div>
          )}
          <div className="flex flex-col gap-3 rounded-xl border border-line bg-white px-5 py-[18px] shadow-[0_1px_3px_rgba(15,42,92,.05)]">
            <div className="text-base font-extrabold text-navy">문서 검색</div>
            <div className="flex gap-2">
              <input
                type="text"
                value={query}
                onChange={(e) => changeQuery(e.target.value)}
                placeholder="질문 또는 키워드 입력"
                className="min-w-0 flex-1 rounded-lg border border-line-input px-[13px] py-2.5 text-sm font-medium text-ink"
              />
              <button className="cursor-pointer rounded-lg border-none bg-brand px-[18px] py-2.5 font-sans text-sm font-extrabold text-white hover:bg-brand-light">
                검색
              </button>
            </div>
            <div className="flex self-start overflow-hidden rounded-lg border border-line-input bg-white">
              {DOC_FILTERS.map((l) => (
                <div
                  key={l}
                  onClick={() => changeFilter(l)}
                  className="cursor-pointer px-3.5 py-[7px] font-mono text-[13px] font-bold"
                  style={filter === l ? { background: '#1E5FC2', color: '#FFFFFF' } : { background: '#FFFFFF', color: '#475569' }}
                >
                  {l}
                </div>
              ))}
            </div>
            <div className="flex flex-col gap-1.5">
              {DOC_CHIPS.map((q) => (
                <div
                  key={q}
                  onClick={() => changeQuery(q)}
                  className="cursor-pointer self-start rounded-full border border-[#BFDBFE] bg-[#F0F6FF] px-3.5 py-1.5 text-[13px] font-bold text-brand hover:bg-[#DBEAFE]"
                >
                  {q}
                </div>
              ))}
            </div>
            {results.length === 0 ? (
              <div className="rounded-[10px] border-2 border-dashed border-line-input bg-page px-5 py-7 text-center">
                <div className="text-[15px] font-extrabold text-navy">질문을 입력하거나 예시 질문을 선택하세요</div>
                <div className="mt-1.5 text-[13.5px] font-semibold text-slate">top_k=4 결과 카드가 표시됩니다</div>
              </div>
            ) : (
              <div className="flex flex-col gap-2.5">
                {results.map((d, i) => (
                  <div
                    key={d.doc + d.section}
                    className="rounded-[10px] border border-line px-[15px] py-[13px] transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-[0_6px_16px_rgba(15,42,92,.1)]"
                    style={{ animation: `om-fadein .3s ${i * 0.08}s both` }}
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[13px] font-extrabold text-navy">{d.doc}</span>
                      <span
                        className="rounded-[5px] px-2 py-0.5 font-mono text-[10.5px] font-extrabold"
                        style={
                          d.model === 'COMMON'
                            ? { background: '#F1F5F9', color: '#475569' }
                            : { background: '#EDF2FA', color: '#1E5FC2' }
                        }
                      >
                        {d.model}
                      </span>
                    </div>
                    <div className="mt-[5px] text-[13.5px] font-bold text-ink">{d.section}</div>
                    <div className="mt-[7px] flex items-center gap-2">
                      <span className="font-mono text-[11px] font-bold text-slate-light">score {d.score.toFixed(2)}</span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded-[3px] bg-line-soft">
                        <div className="h-full rounded-[3px] bg-brand" style={{ width: `${d.score * 100}%` }} />
                      </div>
                    </div>
                    {d.excerpt && (
                      <div className="mt-[9px] border-l-[3px] border-[#BFDBFE] pl-2.5 text-[13px] font-medium leading-[1.55] text-slate">
                        {d.excerpt}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            <div className="text-xs font-semibold text-slate-light">
              ℹ COMMON 문서(트러블슈팅 가이드)는 model_code 필터와 무관하게 항상 포함됩니다
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default KnowledgePage
