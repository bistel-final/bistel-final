// Detection 화면과 Agent 상세가 함께 쓰는 실측 trace 파생 로직.
// 응답에 없는 값은 만들지 않으며, 센서별 한계선만 사용한다.

export function limitLines(lim) {
  return [
    ['USL', lim?.spec_upper],
    ['UCL', lim?.ctrl_upper],
    ['TARGET', lim?.target],
    ['LCL', lim?.ctrl_lower],
    ['LSL', lim?.spec_lower],
  ]
    .filter(([, value]) => value != null)
    .map(([label, value]) => ({ label, value }))
}

export function judgeValue(value, lim) {
  if (value == null || !lim) return null
  const hasLimit = [lim.spec_lower, lim.ctrl_lower, lim.ctrl_upper, lim.spec_upper].some(
    (candidate) => candidate != null,
  )
  if (!hasLimit) return null
  if (lim.spec_upper != null && value > lim.spec_upper) return 'OOS'
  if (lim.spec_lower != null && value < lim.spec_lower) return 'OOS'
  if (lim.ctrl_upper != null && value > lim.ctrl_upper) return 'OOC'
  if (lim.ctrl_lower != null && value < lim.ctrl_lower) return 'OOC'
  return 'OK'
}

// Recharts' numeric axis starts at 0 by default. Trace values can be concentrated far
// from zero, so that default hides the variation that matters for an alarm. Keep the
// measured range in focus while retaining limit lines that cross it. If every limit is
// outside the measurements, retain only the closest limit on each side for context.
//
// includeAllLimits: USL·UCL·TGT·LCL·LSL 다섯 선을 모두 그려야 하는 화면(알람 히스토리
// 상세 그래프)용. 축 밖으로 밀린 한계선은 recharts가 그리지 않고 버리므로(ReferenceLine
// 기본 ifOverflow='discard') 도메인에 미리 포함시켜야 LCL·LSL이 보인다.
export function traceYAxisDomain(wafers, lim, { includeAllLimits = false } = {}) {
  const measured = wafers
    .flatMap((wafer) => wafer?.points ?? [])
    .map((point) => point?.value)
    .filter(Number.isFinite)

  if (!measured.length) return ['auto', 'auto']

  const measuredMin = Math.min(...measured)
  const measuredMax = Math.max(...measured)
  const limits = limitLines(lim)
    .map((line) => line.value)
    .filter(Number.isFinite)
    .sort((a, b) => a - b)
  const crossingLimits = limits.filter((value) => value >= measuredMin && value <= measuredMax)

  let visibleLimits = includeAllLimits ? limits : crossingLimits
  if (!includeAllLimits && !crossingLimits.length) {
    const below = limits.filter((value) => value < measuredMin).at(-1)
    const above = limits.find((value) => value > measuredMax)
    visibleLimits = [below, above].filter(Number.isFinite)
  }

  const visibleValues = [...measured, ...visibleLimits]
  const visibleMin = Math.min(...visibleValues)
  const visibleMax = Math.max(...visibleValues)
  const span = visibleMax - visibleMin
  const padding = span > 0 ? span * 0.08 : Math.max(Math.abs(visibleMin) * 0.05, 0.01)
  const roundingUnit = 10 ** (Math.floor(Math.log10(Math.max(span, Math.abs(visibleMax), 0.001))) - 3)

  // 축 끝이 실측이 아니라 한계선이면 여백을 줄인다 — 한계선은 축에 닿지 않을 정도만
  // 띄우면 되고, 남은 높이는 실측 변화를 크게 보여주는 데 쓴다.
  const lowerPadding = visibleMin < measuredMin ? padding * 0.35 : padding
  const upperPadding = visibleMax > measuredMax ? padding * 0.35 : padding
  return [
    Math.floor((visibleMin - lowerPadding) / roundingUnit) * roundingUnit,
    Math.ceil((visibleMax + upperPadding) / roundingUnit) * roundingUnit,
  ]
}

const tracePointIdentity = (point, pointIndex) =>
  `${point?.recipe_step_no ?? point?.recipe_step_name ?? 'STEP'}:${point?.seq_no ?? pointIndex}`

const tracePointLabel = (point, pointIndex) =>
  `${point?.recipe_step_name ?? `Step ${point?.recipe_step_no ?? '—'}`} · seq ${point?.seq_no ?? pointIndex}`

export const formatMeasuredAt = (value) => {
  if (!value) return null
  const measuredAt = new Date(value)
  if (Number.isNaN(measuredAt.getTime())) return null
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(measuredAt)
  const part = Object.fromEntries(parts.map(({ type, value: item }) => [type, item]))
  return `${part.year}.${part.month}.${part.day} ${part.hour}:${part.minute}:${part.second}`
}

const measuredTimeLabel = (point, pointIndex) => {
  const formatted = formatMeasuredAt(point?.measured_at)
  if (!formatted) return tracePointLabel(point, pointIndex)
  return formatted.split(' ')[1]
}

// X축은 wafer, 선은 동일 측정 위치(step + seq)다. 이 방향이어야 wafer 간
// 변화가 바로 비교되고, 원시 측정값도 평균으로 축약하지 않고 모두 유지된다.
export function traceChartModel(wafers) {
  const seriesByKey = new Map()
  for (const wafer of wafers) {
    ;(wafer?.points ?? []).forEach((point, pointIndex) => {
      const key = tracePointIdentity(point, pointIndex)
      if (!seriesByKey.has(key)) {
        seriesByKey.set(key, { key, label: tracePointLabel(point, pointIndex) })
      }
    })
  }

  const series = [...seriesByKey.values()]
  const rows = wafers.map((wafer, waferIndex) => {
    const row = {
      wafer_key: wafer?.lot_hist_id ?? `WAFER-${waferIndex + 1}`,
      wafer_label: `W${wafer?.wafer_no ?? waferIndex + 1}`,
      wafer_no: wafer?.wafer_no ?? waferIndex + 1,
    }
    ;(wafer?.points ?? []).forEach((point, pointIndex) => {
      const key = tracePointIdentity(point, pointIndex)
      row[key] = point?.value
      row[`${key}:meta`] = point
    })
    return row
  })

  return { rows, series }
}

// 알람 행을 클릭한 직후에는 해당 wafer 자체의 공정 내 측정 흐름을 먼저 보여 준다.
// measured_at 실제 시각을 X축으로 두고 하나의 실측선으로 연결해야 사용자가 클릭한
// 알람의 이탈 위치를 바로 읽을 수 있다. LOT·chamber 비교는 별도 보기다.
export function selectedWaferChartModel(wafer) {
  if (!wafer) return { rows: [], series: [] }
  const waferLabel = `W${wafer.wafer_no ?? '—'}`
  const rows = (wafer.points ?? []).map((point, pointIndex) => ({
    point_key: tracePointIdentity(point, pointIndex),
    point_label: measuredTimeLabel(point, pointIndex),
    wafer_label: waferLabel,
    wafer_no: wafer.wafer_no,
    value: point?.value,
    'value:meta': point,
  }))
  return {
    rows,
    series: rows.length ? [{ key: 'value', label: `${waferLabel} 실측` }] : [],
  }
}

export function detailNumbers(detail) {
  const grab = (key) => {
    const match = String(detail ?? '').match(new RegExp(`${key}\\s+(-?[0-9.]+)`))
    return match ? Number(match[1]) : null
  }
  return { mean: grab('mean'), min: grab('min'), max: grab('max') }
}
