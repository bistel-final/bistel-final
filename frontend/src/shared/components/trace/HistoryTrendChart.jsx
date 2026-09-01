import { useState } from 'react'
import { Card } from '../ui/Card.jsx'
import { judgeValue } from '../../trace/traceModel.js'
import TraceChart from './TraceChart.jsx'

// 웨이퍼 격자에서도 채도는 이상 신호에만 쓴다.
// 정상까지 점을 찍으면 수십 개 버튼이 전부 색을 갖게 돼 OOS·OOC가 묻힌다.
// 정상은 점 없음(자리만 유지), 미확인은 빈 링으로 "측정 없음"과 구분한다.
const WAFER_STATUS = {
  OOS: { label: 'OOS', text: 'text-trace-oos', dot: 'bg-trace-oos' },
  OOC: { label: 'OOC', text: 'text-trace-ooc', dot: 'bg-trace-ooc' },
  OK: { label: '정상', text: 'text-navy', dot: '' },
  UNKNOWN: { label: '미확인', text: 'text-g2', dot: 'border border-dash-line' },
}

function statusOfWafer(wafer, limit) {
  const statuses = (wafer?.points ?? []).map((point) => judgeValue(point.value, limit))
  if (statuses.includes('OOS')) return 'OOS'
  if (statuses.includes('OOC')) return 'OOC'
  if (statuses.includes('OK')) return 'OK'
  return 'UNKNOWN'
}

function waferSummary(wafer, limit) {
  const counts = { OOS: 0, OOC: 0, OK: 0, UNKNOWN: 0 }
  const values = []
  for (const point of wafer?.points ?? []) {
    counts[judgeValue(point.value, limit) ?? 'UNKNOWN'] += 1
    if (Number.isFinite(point.value)) values.push(point.value)
  }
  const status = statusOfWafer(wafer, limit)
  const unit = limit?.unit ? ` ${limit.unit}` : ''
  const range = values.length ? `${Math.min(...values)} ~ ${Math.max(...values)}${unit}` : '실측 미제공'
  return { counts, range, status }
}

function boundaryAlert(wafer, limit) {
  const values = (wafer?.points ?? []).map((point) => point?.value).filter(Number.isFinite)
  const upperOos = values.filter((value) => Number.isFinite(limit?.spec_upper) && value > limit.spec_upper).length
  const lowerOos = values.filter((value) => Number.isFinite(limit?.spec_lower) && value < limit.spec_lower).length
  if (upperOos) return { status: 'OOS', text: `USL ${limit.spec_upper} 초과 · OOS ${upperOos} point` }
  if (lowerOos) return { status: 'OOS', text: `LSL ${limit.spec_lower} 미만 · OOS ${lowerOos} point` }
  const upperOoc = values.filter((value) => Number.isFinite(limit?.ctrl_upper) && value > limit.ctrl_upper).length
  const lowerOoc = values.filter((value) => Number.isFinite(limit?.ctrl_lower) && value < limit.ctrl_lower).length
  if (upperOoc) return { status: 'OOC', text: `UCL ${limit.ctrl_upper} 초과 · OOC ${upperOoc} point` }
  if (lowerOoc) return { status: 'OOC', text: `LCL ${limit.ctrl_lower} 미만 · OOC ${lowerOoc} point` }
  return null
}

function LotWaferPanel({ alarm, wafers, limit, selectedWafer, onSelect }) {
  const counts = { OOS: 0, OOC: 0, OK: 0, UNKNOWN: 0 }
  const chamberGroups = new Map()
  for (const item of wafers) {
    counts[statusOfWafer(item, limit)] += 1
    if (!chamberGroups.has(item.chamber_id)) chamberGroups.set(item.chamber_id, [])
    chamberGroups.get(item.chamber_id).push(item)
  }
  const selectedSummary = waferSummary(selectedWafer, limit)
  return (
    <aside className="h-full min-h-0 overflow-y-auto rounded-xl border border-line bg-soft p-3.5">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-mono text-[12px] font-extrabold text-navy">{alarm.lot_id} · {alarm.sensor_id ?? alarm.parameter_id}</div>
          <div className="mt-0.5 text-[10.5px] text-g2">LOT 웨이퍼 현황 · 선택은 그래프만 변경</div>
        </div>
        <span className="rounded-md border border-tint-blue-line bg-tint-blue px-2 py-1 font-mono text-[10px] font-bold text-blue">전체 {wafers.length}</span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-1.5">
        {['OOS', 'OOC', 'OK'].map((status) => (
          <div key={status} className="rounded-md border border-line bg-white px-2 py-2 text-center">
            <div className={`font-mono text-[16px] font-extrabold ${WAFER_STATUS[status].text}`}>{counts[status]}</div>
            <div className="text-[9.5px] font-bold text-g2">{WAFER_STATUS[status].label}</div>
          </div>
        ))}
      </div>
      <div className="mt-2.5 rounded-lg border border-tint-blue-line bg-tint-blue px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="text-[9.5px] font-bold text-g2">현재 그래프</div>
            <div className="mt-0.5 font-mono text-[14px] font-extrabold text-blue">W{selectedWafer?.wafer_no}</div>
          </div>
          <span className={`text-[10px] font-extrabold ${WAFER_STATUS[selectedSummary.status].text}`}>
            {WAFER_STATUS[selectedSummary.status].label}
          </span>
        </div>
        <div className="mt-2 text-[10px] text-g2">
          <span className="font-mono text-g1">{selectedWafer?.chamber_id}</span>
          <span> · {selectedWafer?.points?.length ?? 0} point · {selectedSummary.range}</span>
        </div>
        <div className="mt-1.5 flex gap-3 font-mono text-[9.5px] text-g2">
          <span>OOS <strong className={selectedSummary.counts.OOS ? WAFER_STATUS.OOS.text : 'text-g1'}>{selectedSummary.counts.OOS}</strong></span>
          <span>OOC <strong className={selectedSummary.counts.OOC ? WAFER_STATUS.OOC.text : 'text-g1'}>{selectedSummary.counts.OOC}</strong></span>
          <span>정상 <strong className="text-g1">{selectedSummary.counts.OK}</strong></span>
        </div>
      </div>
      <div className="my-3 h-px bg-line" />
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10.5px] font-bold text-g1">웨이퍼 선택</span>
        <span className="font-mono text-[10px] text-g2">기준 알람 W{alarm.wafer_no}</span>
      </div>
      <div className="space-y-2.5">
        {[...chamberGroups].map(([chamber, items]) => (
          <div key={chamber} className="rounded-lg border border-line bg-white p-2.5">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-mono text-[10px] font-bold text-navy">{chamber}</span>
              <span className="text-[9.5px] text-g2">{items.length}장</span>
            </div>
            <div className="grid grid-cols-4 gap-1.5">
              {items.map((item) => {
                const status = statusOfWafer(item, limit)
                const selected = item.lot_hist_id === selectedWafer?.lot_hist_id
                return (
                  <button
                    key={item.lot_hist_id}
                    type="button"
                    aria-pressed={selected}
                    title={`W${item.wafer_no} · ${item.chamber_id} · ${WAFER_STATUS[status].label}`}
                    className={`flex h-8 items-center justify-center rounded-lg border font-mono text-[10.5px] font-bold transition ${selected ? 'border-blue bg-tint-blue text-blue' : 'border-line bg-white text-g1 hover:border-blue hover:text-blue'}`}
                    onClick={() => onSelect(item.lot_hist_id)}
                  >
                    <span className={`mr-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${WAFER_STATUS[status].dot}`} aria-hidden="true" />
                    W{item.wafer_no}
                  </button>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}

function sensorGroups(response, fallbackWafer, fallbackLimit) {
  const wafers = response?.wafers ?? (fallbackWafer ? [fallbackWafer] : [])
  const groups = new Map()
  for (const wafer of wafers) {
    const sensor = wafer.sensor_id ?? fallbackWafer?.sensor_id ?? 'PARAMETER'
    if (!groups.has(sensor)) groups.set(sensor, [])
    groups.get(sensor).push(wafer)
  }
  return [...groups].map(([sensor, items]) => ({
    sensor,
    wafers: items,
    limit: response ? response.limits?.[sensor] ?? null : fallbackLimit ?? null,
  }))
}

export function HistoryTrendChart({ wafer, lim, response = null, emptyMessage = null, highlightWaferNo = null, viewMode = 'context' }) {
  const groups = sensorGroups(response, wafer, lim)
  if (!groups.length) {
    return <div className="flex h-[300px] items-center justify-center rounded-[10px] border-[1.5px] border-dashed border-dash-line text-[12.5px] text-g2">{emptyMessage ?? '선택한 incident의 trace 실측이 응답에 없습니다'}</div>
  }
  const fillsContainer = viewMode === 'selected'
  return (
    <div className={`flex flex-col gap-3${fillsContainer ? ' h-full min-h-0' : ''}`}>
      {groups.map((group) => {
        const selected = group.wafers.find((item) =>
          wafer?.lot_hist_id
            ? item.lot_hist_id === wafer.lot_hist_id
            : Number(item.wafer_no) === Number(wafer?.wafer_no),
        ) ?? wafer
        const chartWafers = viewMode === 'selected' ? [selected].filter(Boolean) : group.wafers
        const pointCount = selected?.points?.length ?? 0
        const alert = viewMode === 'selected' ? boundaryAlert(selected, group.limit) : null
        return (
          <div key={group.sensor} className={`rounded-lg border border-cell-line bg-white px-2 pt-2${fillsContainer ? ' flex h-full min-h-0 flex-col' : ''}`}>
            <div className="flex items-center justify-between gap-3 px-2">
              <span className="font-mono text-[11px] font-bold text-navy">
                {group.sensor} · {viewMode === 'selected' ? `W${selected?.wafer_no ?? '—'} · ${selected?.chamber_id ?? 'CHAMBER 미제공'} · ${pointCount} point` : `${group.wafers.length} wafer`}
                {group.limit?.unit ? ` · ${group.limit.unit}` : ''}
              </span>
              <span className="flex items-center gap-2">
                {alert && (
                  <span className={`rounded-md border px-2 py-1 text-[10px] font-bold ${
                    alert.status === 'OOS'
                      ? 'border-tint-red-line bg-trace-oos-zone text-trace-oos'
                      : 'border-tint-amber-line bg-trace-ooc-zone text-trace-ooc'
                  }`}>
                    {alert.text}
                  </span>
                )}
                <span className="text-[10.5px] text-g2">
                  {viewMode === 'selected' ? 'X축: 실제 측정 시각 · 툴팁: 공정/seq' : 'X축: 웨이퍼 · 색상선: 공정 단계별 측정 순번'}
                </span>
              </span>
            </div>
            {fillsContainer ? (
              <div className="min-h-[280px] flex-1">
                <TraceChart wafers={chartWafers} limit={group.limit} height="100%" syncId="incident-trace" highlightWaferNo={highlightWaferNo} viewMode={viewMode} />
              </div>
            ) : (
              <TraceChart wafers={chartWafers} limit={group.limit} height={groups.length > 1 ? 245 : 300} syncId="incident-trace" highlightWaferNo={highlightWaferNo} viewMode={viewMode} />
            )}
          </div>
        )
      })}
    </div>
  )
}

export function HistoryTrendCard({ alarm, wafer, lim, response = null, loading, emptyMessage = null, actions = null, lotWaferCount = null, allowWaferSelection = false }) {
  const [waferSelection, setWaferSelection] = useState({ alarmId: alarm?.alarm_id ?? null, lotHistId: wafer?.lot_hist_id ?? null })
  const parameter = alarm?.parameter_id ?? alarm?.sensor_id
  const waferLabel = alarm?.wafer_id ?? (alarm?.wafer_no != null ? `W${alarm.wafer_no}` : null)
  const groupCount = response?.wafers?.length ?? (wafer ? 1 : 0)
  const selectableWafers = response?.wafers ?? (wafer ? [wafer] : [])
  const defaultWafer = selectableWafers.find((item) => item.lot_hist_id === wafer?.lot_hist_id) ?? wafer ?? selectableWafers[0] ?? null
  const selectedLotHistId = waferSelection.alarmId === alarm?.alarm_id
    ? waferSelection.lotHistId
    : defaultWafer?.lot_hist_id
  const selectedWafer = selectableWafers.find((item) => item.lot_hist_id === selectedLotHistId) ?? defaultWafer
  const scopeLabel = lotWaferCount
    ? `동일 챔버 ${groupCount}장 / LOT 전체 ${lotWaferCount}장`
    : `동일 챔버 ${groupCount}장`
  return (
    <Card className="px-5 pb-3 pt-4">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <span className="font-mono text-[14px] font-extrabold text-ink">
          {alarm ? `${allowWaferSelection ? '기준 알람 · ' : ''}${parameter ?? 'PARAMETER 미제공'} · ${waferLabel ?? 'WAFER 미제공'} · ${alarm.chamber_id}` : '선택 알람 트렌드'}
        </span>
        <span className="flex items-center gap-2.5">
          {!allowWaferSelection && <span className="text-[11.5px] text-g2">{alarm ? scopeLabel : emptyMessage ?? '행을 선택하면 트렌드가 표시됩니다'}</span>}
          {alarm ? actions : null}
        </span>
      </div>
      {loading ? <div className="flex h-[300px] items-center justify-center text-[12.5px] text-g2">트렌드를 불러오는 중…</div> : alarm ? (
        allowWaferSelection ? (
          <div className="grid gap-3 lg:h-[500px] lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="min-w-0 lg:h-full">
              <HistoryTrendChart wafer={selectedWafer} lim={lim} response={response} emptyMessage={emptyMessage} highlightWaferNo={alarm.wafer_no} viewMode="selected" />
            </div>
            <LotWaferPanel
              alarm={alarm}
              wafers={selectableWafers}
              limit={lim}
              selectedWafer={selectedWafer}
              onSelect={(lotHistId) => setWaferSelection({ alarmId: alarm.alarm_id, lotHistId })}
            />
          </div>
        ) : <HistoryTrendChart wafer={selectedWafer} lim={lim} response={response} emptyMessage={emptyMessage} highlightWaferNo={alarm.wafer_no} viewMode="context" />
      ) : <div className="flex h-[300px] items-center justify-center rounded-[10px] border-[1.5px] border-dashed border-dash-line text-[12.5px] text-g2">{emptyMessage ?? '테이블에서 알람 행을 선택해 주세요'}</div>}
    </Card>
  )
}

export default HistoryTrendChart
