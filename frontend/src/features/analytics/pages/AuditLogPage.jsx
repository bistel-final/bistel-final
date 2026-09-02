import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAuditLogs } from '../../../shared/api/analytics.js'
import { getRunsCore } from '../../../shared/api/agent.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import AuditFilterBar from '../components/AuditFilterBar.jsx'
import { ALL, DEFAULT_AUDIT_FILTER, buildRunContext, runContextFromRuns } from '../components/auditModel.js'
import AuditEventTypeBars from '../components/AuditEventTypeBars.jsx'
import AuditTable from '../components/AuditTable.jsx'
import AuditDetailDrawer from '../components/AuditDetailDrawer.jsx'
import Pagination from '../../../shared/components/ui/Pagination.jsx'

// 감사로그 — 라이트 시안 7번. 필터 변경 즉시 재조회 (조회 버튼 없음).
// 좌 270px 유형별 집계 + 우측 테이블 + 행 클릭 상세 드로어(before/after 원본).
// 유형 목록·집계는 응답 event_types / event_type_counts 그대로 쓴다.
const PAGE_SIZE = 10 // 감사 이벤트 규모(수십건)에 맞춘 페이지 크기 — 서버 pagination

function AuditLogPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState(() => ({
    ...DEFAULT_AUDIT_FILTER,
    target: searchParams.get('entity_id')?.trim() ?? '',
  }))
  // 서버 pagination (V5-D-1.4) — 정렬은 서버가 occurred_at DESC, audit_id DESC 로 보증한다.
  // 필터가 바뀌면 1페이지로 돌아간다 (직전 페이지가 새 조건에서는 범위 밖일 수 있다).
  const [page, setPage] = useState(1)
  const changeFilter = (next) => {
    const nextParams = new URLSearchParams(searchParams)
    if (next.target.trim()) nextParams.set('entity_id', next.target.trim())
    else nextParams.delete('entity_id')
    setSearchParams(nextParams, { replace: true })
    setFilter(next)
    setPage(1)
  }
  // 상세 드로어 — 선택 audit_id 만 기억한다. 페이지·필터가 바뀌어 목록에서 사라지면 자동으로 닫힌다.
  const [selectedId, setSelectedId] = useState(null)
  // 대상 열 표기용 실행 문맥 — Agent 화면과 같은 원천(GET /agent/runs)에서 한 번만 읽는다.
  // 부수 조회라 실패해도 감사 화면은 그대로 동작하고, 그때는 감사 이벤트 자체의 알람 정보로 보결한다.
  const [runsContext, setRunsContext] = useState(() => new Map())
  useEffect(() => {
    const controller = new AbortController()
    getRunsCore({}, { signal: controller.signal })
      .then((rows) => setRunsContext(runContextFromRuns(rows)))
      .catch(() => {})
    return () => controller.abort()
  }, [])
  // ↑↓ 로 인접 이벤트 이동 (드로어가 열려 있을 때만). Esc 는 드로어가 직접 처리한다.
  useEffect(() => {
    if (selectedId == null || !data) return undefined
    const onKey = (ev) => {
      if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return
      const list = data.items ?? []
      const i = list.findIndex((e) => e.audit_id === selectedId)
      if (i < 0) return
      const next = list[i + (ev.key === 'ArrowDown' ? 1 : -1)]
      if (next) {
        ev.preventDefault()
        setSelectedId(next.audit_id)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedId, data])

  const load = useCallback(() => {
    getAuditLogs({
      event_type: filter.type === ALL ? undefined : filter.type,
      actor_type: filter.actor === ALL ? undefined : filter.actor,
      entity_id: filter.target.trim() || undefined,
      date_from: filter.from || undefined,
      date_to: filter.to || undefined,
      page,
      size: PAGE_SIZE,
    })
      .then((res) => setData(res))
      .catch((e) => setError(e.message))
  }, [filter, page])
  useEffect(() => {
    load()
  }, [load])

  const counts = useMemo(() => data?.event_type_counts ?? {}, [data])

  if (error)
    return (
      <ErrorState
        detail={error}
        onRetry={() => {
          setError(null)
          load()
        }}
      />
    )
  if (!data) return <LoadingState message="감사로그를 불러오는 중…" />

  const items = data.items ?? []
  const runContext = buildRunContext(items, runsContext)
  const selected = items.find((e) => e.audit_id === selectedId) ?? null
  const eventTypes = data.event_types ?? []
  const total = data.total ?? items.length
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center pb-1.5 pt-3.5">
        <div className="text-[20px] font-extrabold text-ink">감사로그</div>
      </div>

      <AuditFilterBar eventTypes={eventTypes} value={filter} onChange={changeFilter} />

      <div className="flex items-start gap-4">
        <AuditEventTypeBars eventTypes={eventTypes} counts={counts} total={data.total ?? items.length} />

        <Card className="min-w-0 flex-1">
          <CardHeader title="이벤트" />
          {items.length === 0 ? (
            <EmptyState title="조건에 맞는 감사 이벤트가 없습니다" description="기간·유형·주체·대상 필터를 조정해 주세요." />
          ) : (
            <>
              <AuditTable items={items} runContext={runContext} selectedId={selectedId} onSelect={(e) => setSelectedId(e.audit_id)} />
              {pageCount > 1 && (
                <Pagination
                  page={page}
                  pageCount={pageCount}
                  rangeLabel={`${(page - 1) * PAGE_SIZE + 1} – ${Math.min(page * PAGE_SIZE, total)} / ${total}`}
                  onPage={setPage}
                />
              )}
            </>
          )}
        </Card>
      </div>

      <AuditDetailDrawer
        event={selected}
        items={items}
        runContext={runContext}
        onSelect={(e) => setSelectedId(e.audit_id)}
        onClose={() => setSelectedId(null)}
      />
    </div>
  )
}

export default AuditLogPage
