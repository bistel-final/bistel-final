import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { getAlarm, getAllAlarms, getDashboard, getTraceCatalog, searchTraces } from '../../../shared/api/detection.js'
import { createRun, getActions } from '../../../shared/api/agent.js'
import { fmtShort } from '../../../shared/api/format.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import Pagination from '../../../shared/components/ui/Pagination.jsx'
import AlarmTracePanel from '../../../shared/components/trace/AlarmTracePanel.jsx'
import { detailNumbers } from '../../../shared/trace/traceModel.js'
import { alarmTrendScope } from '../../../shared/trace/incidentTrace.js'
import {
  CELL_DIM,
  CELL_ID,
  CELL_MONO,
  TD_CLS,
  TH_CLS,
  judgementClass,
  rowClass,
  ruleVariant,
} from '../../../shared/components/ui/statusStyles.js'
import { sensorLimit } from '../components/TraceModel.jsx'
import ScopeFilterBar from '../components/ScopeFilterBar.jsx'
import { ALL, DEFAULT_SCOPE } from '../components/scopeModel.js'
import { analysisActionOf, partitionAlarms, runErrorMessage } from '../detection-screen-state.js'

// 알람 히스토리 — 라이트 시안 2번. 상단 선택 알람 트렌드 + 필터바 + TRACE/SUMMARY/R03 탭 + 테이블.
// 탭 분리는 source(AlarmRef의 TRACE·SUMMARY·R03)로 한다 — judgement로 나누면 R03(OOS)이
// TRACE 탭에 섞여 보이지 않는 것처럼 된다.
// 기간·탭 분리 목록 파라미터가 명세에 없어 넓게 받아 클라이언트에서 나눈다. TODO(api): 기간·source 파라미터
const WIDE = 100
const PAGE_SIZE = 12

const dateOf = (iso) => String(iso ?? '').slice(0, 10)

// 이탈 방향의 실측값 — 상한 초과면 max, 하한 미달이면 min (detail 실측에서만 뽑는다)
function alarmValue(alarm, lim) {
  const { mean, min, max } = detailNumbers(alarm.detail)
  if (max != null && lim?.spec_upper != null && max > lim.spec_upper) return max
  if (min != null && lim?.spec_lower != null && min < lim.spec_lower) return min
  if (max != null && lim?.ctrl_upper != null && max > lim.ctrl_upper) return max
  if (min != null && lim?.ctrl_lower != null && min < lim.ctrl_lower) return min
  return max ?? mean ?? null
}

const num = (v) => (v == null ? '—' : Number(v).toFixed(v % 1 === 0 ? 0 : 3))

function AlarmsPage() {
  const { alarmId } = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [data, setData] = useState(null)
  const [trace, setTrace] = useState(null)
  const [error, setError] = useState(null)
  const [draft, setDraft] = useState(DEFAULT_SCOPE)
  const [applied, setApplied] = useState(DEFAULT_SCOPE)
  const [page, setPage] = useState(1)
  const [runPending, setRunPending] = useState(false)
  const [runError, setRunError] = useState(null)
  const tabParam = searchParams.get('tab')
  const tab = ['TRACE', 'SUMMARY', 'R03'].includes(tabParam) ? tabParam : 'TRACE'
  const sourceQuery = searchParams.get('source')
  const source = ['TRACE', 'SUMMARY', 'R03'].includes(sourceQuery) ? sourceQuery : null
  const invalidSource = sourceQuery !== null && source === null

  const load = useCallback(() => {
    const scope = {}
    if (applied.area !== ALL) scope.area = applied.area
    if (applied.equipment !== ALL) scope.equipment_id = applied.equipment
    if (applied.chamber !== ALL) scope.chamber_id = applied.chamber
    Promise.all([
      getDashboard({}),
      getTraceCatalog(),
      getAllAlarms(scope), // size 상한(100)보다 총 건수가 많을 수 있어 전체 페이지를 순회한다
      getActions({ page: 1, size: WIDE }),
      alarmId && source ? getAlarm(alarmId, source) : Promise.resolve(null),
    ])
      .then(([summary, catalog, alarmPage, actionPage, focusedAlarm]) =>
        setData({
          hierarchy: summary.hierarchy ?? [],
          catalog,
          alarms: alarmPage.items ?? [],
          actionOf: Object.fromEntries((actionPage.items ?? []).map((a) => [a.action_id, a])),
          focusedAlarm,
        }),
      )
      .catch((e) => setError(e.message))
  }, [alarmId, applied, source])
  useEffect(() => {
    load()
  }, [load])

  // 선택 알람 트렌드 — /alarms/:alarmId 로 복원된다
  const selected = useMemo(
    () => {
      if (!alarmId || invalidSource) return null
      if (source) return data?.focusedAlarm ?? null
      return (data?.alarms ?? []).find((alarm) => alarm.alarm_id === alarmId) ?? null
    },
    [alarmId, data, invalidSource, source],
  )
  const loadTrace = useCallback(() => {
    if (!selected) return
    const scope = alarmTrendScope(selected)
    const request = scope ? searchTraces(scope) : Promise.resolve(null)
    request
      .then((res) =>
        setTrace({
          forId: selected.alarm_id,
          response: res,
          wafer: res?.wafers?.find(
            (item) => item.sensor_id === selected.sensor_id && Number(item.wafer_no) === Number(selected.wafer_no),
          ) ?? res?.wafers?.[0] ?? null,
          lim: res?.limits?.[selected.sensor_id] ?? null,
        }),
      )
      .catch((e) => setError(e.message))
  }, [selected])
  useEffect(() => {
    loadTrace()
  }, [loadTrace])

  const handleRunAnalysis = () => {
    if (!selected || runPending) return
    const analysisAction = analysisActionOf(selected)
    if (analysisAction.mode === 'OPEN') {
      setRunError(null)
      navigate(`/agent-runs/${encodeURIComponent(analysisAction.runId)}`)
      return
    }
    setRunPending(true)
    setRunError(null)
    createRun({ alarm: { source: selected.source, alarm_id: selected.alarm_id } })
      .then((accepted) => {
        navigate(`/agent-runs/${accepted.agent_run_id}`)
      })
      .catch((error) => setRunError(runErrorMessage(error)))
      .finally(() => setRunPending(false))
  }

  const rows = useMemo(() => {
    if (!data) return { trace: [], summary: [], r03: [] }
    const inRange = (a) => {
      const d = dateOf(a.occurred_at)
      return (!applied.from || d >= applied.from) && (!applied.to || d <= applied.to)
    }
    return partitionAlarms(data.alarms.filter(inRange))
  }, [data, applied])

  const retry = () => {
    setError(null)
    setData(null)
    load()
  }

  if (error) return <ErrorState detail={error} onRetry={retry} />
  if (!data) return <LoadingState message="알람 히스토리를 불러오는 중…" />

  const list = tab === 'TRACE' ? rows.trace : tab === 'SUMMARY' ? rows.summary : rows.r03
  const useSpecLimits = tab !== 'SUMMARY'
  const pageCnt = Math.max(1, Math.ceil(list.length / PAGE_SIZE))
  const curPage = Math.min(page, pageCnt)
  const paged = list.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE)

  const setTab = (next) => {
    setPage(1)
    const params = new URLSearchParams(searchParams)
    params.set('tab', next)
    setSearchParams(params, { replace: true })
  }
  const select = (id) => {
    // 다른 알람을 고르면 이전 알람의 실행 실패 메시지를 들고 있지 않는다
    setRunError(null)
    const selectedSource = data.alarms.find((alarm) => alarm.alarm_id === id)?.source
    const query = new URLSearchParams({ tab })
    if (selectedSource) query.set('source', selectedSource)
    navigate(`/alarms/${encodeURIComponent(id)}?${query}`)
  }

  const limOf = (sensorId) => sensorLimit(null, data.catalog, sensorId)
  const shownTrace = trace?.forId === alarmId ? trace : null
  const analysisAction = analysisActionOf(selected)

  const tabCls = (on) =>
    `inline-flex h-8 cursor-pointer items-center rounded-lg border px-3.5 font-mono text-[12px] font-bold ${
      on ? 'border-blue bg-tint-blue text-blue-hover' : 'border-field-line bg-white text-g2'
    }`

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[20px] font-extrabold text-ink">알람 히스토리</div>
        <div className="text-[11.5px] text-g2">
          기간 내 <span className="font-mono">{rows.trace.length + rows.summary.length + rows.r03.length}</span>건
        </div>
      </div>

      <AlarmTracePanel
        alarm={selected}
        wafer={shownTrace?.wafer}
        lim={shownTrace?.lim ?? (selected ? limOf(selected.sensor_id) : null)}
        response={shownTrace?.response}
        loading={Boolean(selected) && !shownTrace}
        allowWaferSelection
        lotWaferCount={
          selected
            ? data.catalog?.lots?.find((lot) => lot.lot_id === selected.lot_id)?.wafer_nos?.length ?? null
            : null
        }
        actions={
          <span className="flex items-center gap-2">
            {runError && <span className="text-[11px] font-semibold text-red">{runError}</span>}
            <Button sm onClick={handleRunAnalysis} disabled={runPending}>
              {runPending ? '분석 실행 중…' : analysisAction.label}
            </Button>
          </span>
        }
      />

      <div className="mt-4">
        <ScopeFilterBar
          hierarchy={data.hierarchy}
          draft={draft}
          onDraft={setDraft}
          onApply={() => {
            setPage(1)
            setApplied(draft)
          }}
          onReset={() => {
            setPage(1)
            setDraft(DEFAULT_SCOPE)
            setApplied(DEFAULT_SCOPE)
          }}
        />
      </div>

      <div className="mb-3 flex items-center gap-2">
        <button type="button" className={tabCls(tab === 'TRACE')} onClick={() => setTab('TRACE')}>
          TRACE · OOS ({rows.trace.length})
        </button>
        <button type="button" className={tabCls(tab === 'SUMMARY')} onClick={() => setTab('SUMMARY')}>
          SUMMARY · OOC ({rows.summary.length})
        </button>
        <button type="button" className={tabCls(tab === 'R03')} onClick={() => setTab('R03')}>
          R03 · 연속 OOS ({rows.r03.length})
        </button>
      </div>

      <Card>
        <CardHeader
          title={tab === 'TRACE' ? 'TRACE 알람' : tab === 'SUMMARY' ? 'SUMMARY 알람' : 'R03 알람'}
          note="청색 행은 기준 알람 · 행 클릭 시 기준 변경"
        />
        {paged.length === 0 ? (
          <EmptyState title="조건에 맞는 알람이 없습니다" description="기간·AREA·설비·챔버 필터를 조정해 주세요." />
        ) : (
          <div className="overflow-x-auto px-2">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  {['TIME', 'ALARM', 'LOT', 'W', 'EQP-CH', 'PARAMETER', 'STEP', 'RULE', 'HIT', 'VALUE', useSpecLimits ? 'LSL' : 'LCL', useSpecLimits ? 'USL' : 'UCL', 'NOTIFY'].map((h) => (
                    <th key={h} className={TH_CLS}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {paged.map((a, i) => {
                  const lim = limOf(a.sensor_id)
                  const value = alarmValue(a, lim)
                  const act = a.action_id ? data.actionOf[a.action_id] : null
                  return (
                    <tr
                      key={a.alarm_id}
                      onClick={() => select(a.alarm_id)}
                      className={`cursor-pointer ${rowClass(i, { sel: alarmId === a.alarm_id })}`}
                    >
                      <td className={`${TD_CLS} ${CELL_DIM}`}>{fmtShort(a.occurred_at)}</td>
                      <td className={`${TD_CLS} ${CELL_ID}`}>{a.alarm_id}</td>
                      <td className={`${TD_CLS} ${CELL_MONO}`}>{a.lot_id}</td>
                      <td className={`${TD_CLS} ${CELL_MONO}`}>{a.wafer_no}</td>
                      <td className={`${TD_CLS} ${CELL_DIM}`}>{a.chamber_id}</td>
                      <td className={`${TD_CLS} ${CELL_MONO} font-semibold`}>{a.sensor_id}</td>
                      <td className={`${TD_CLS} ${CELL_DIM}`}>{a.recipe_step_name}</td>
                      <td className={TD_CLS}>
                        <Badge variant={ruleVariant(a.rule_id)}>{String(a.rule_id).slice(0, 3)}</Badge>
                      </td>
                      <td className={`${TD_CLS} ${CELL_MONO}`}>{a.hit_cnt}</td>
                      <td className={`${TD_CLS} ${CELL_MONO} font-bold ${judgementClass(a.judgement)}`}>{num(value)}</td>
                      <td className={`${TD_CLS} ${CELL_DIM}`}>{num(useSpecLimits ? lim?.spec_lower : lim?.ctrl_lower)}</td>
                      <td className={`${TD_CLS} ${CELL_DIM}`}>{num(useSpecLimits ? lim?.spec_upper : lim?.ctrl_upper)}</td>
                      <td className={`${TD_CLS} ${CELL_MONO} text-[11px] text-g1`}>
                        {act?.deliveries?.length
                          ? act.deliveries.map((delivery) => `${delivery.channel} · ${delivery.status}`).join(' / ')
                          : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        {list.length > 0 && (
          <Pagination
            page={curPage}
            pageCount={pageCnt}
            rangeLabel={`${(curPage - 1) * PAGE_SIZE + 1} – ${Math.min(curPage * PAGE_SIZE, list.length)} / ${list.length}`}
            onPage={setPage}
          />
        )}
      </Card>
    </div>
  )
}

export default AlarmsPage
