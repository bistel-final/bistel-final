import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAuditLogs } from '../../../shared/api/analytics.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import AuditFilterBar from '../components/AuditFilterBar.jsx'
import { ALL, DEFAULT_AUDIT_FILTER } from '../components/auditModel.js'
import AuditEventTypeBars from '../components/AuditEventTypeBars.jsx'
import AuditTable from '../components/AuditTable.jsx'
import Pagination from '../../../shared/components/ui/Pagination.jsx'

// 감사로그 — 라이트 시안 7번. 필터 변경 즉시 재조회 (조회 버튼 없음).
// 좌 270px 유형별 집계 + 우측 테이블. 유형 목록·집계는 응답 event_types / event_type_counts 그대로 쓴다.
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
  // 샘플 ID 칩 — 최초 응답의 distinct entity_id 3개로 고정한다 (필터에 따라 흔들리지 않게)
  const [samples, setSamples] = useState(null)

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
      .then((res) => {
        setData(res)
        setSamples((prev) => prev ?? [...new Set((res.items ?? []).map((e) => e.entity_id))].slice(0, 3))
      })
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
  const eventTypes = data.event_types ?? []
  const total = data.total ?? items.length
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[20px] font-extrabold text-ink">감사로그</div>
        <div className="text-[11.5px] text-g2">
          append-only · 기간 내 <span className="font-mono">{data.total ?? items.length}</span>건
        </div>
      </div>

      <AuditFilterBar
        eventTypes={eventTypes}
        value={filter}
        onChange={changeFilter}
        samples={samples ?? []}
        onReset={() => changeFilter(DEFAULT_AUDIT_FILTER)}
      />

      <div className="flex items-start gap-4">
        <AuditEventTypeBars eventTypes={eventTypes} counts={counts} total={data.total ?? items.length} />

        <Card className="min-w-0 flex-1">
          <CardHeader title="이벤트" note="발생 시각 내림차순" />
          {items.length === 0 ? (
            <EmptyState title="조건에 맞는 감사 이벤트가 없습니다" description="기간·유형·주체·대상 필터를 조정해 주세요." />
          ) : (
            <>
              <AuditTable items={items} />
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
    </div>
  )
}

export default AuditLogPage
