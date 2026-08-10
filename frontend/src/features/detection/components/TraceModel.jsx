// 트레이스 뷰어 파생 로직 — 순수 함수만 모아둔 파일 (컴포넌트 없음)
//
// 데이터 출처는 getTraceCatalog() / getAlarms() 뿐이다. 값 창작 금지:
// 없는 수치는 null 로 돌려주고 화면에서 "실측 미제공"으로 표기한다.

// 설비 prefix → 공정(AREA). 계층은 데이터에서 유도한다 (PHO-01-C1 → PHO-01 → PHOTO)
const AREA_BY_PREFIX = { PHO: 'PHOTO', ETC: 'ETCH' }

export const areaOf = (chamberId) => AREA_BY_PREFIX[String(chamberId ?? '').slice(0, 3)] ?? '기타'
export const equipmentOf = (chamberId) => String(chamberId ?? '').replace(/-C\d+$/, '')

export const uniq = (arr) => [...new Set(arr)]
export const numAsc = (a, b) => Number(a) - Number(b)
export const dateOf = (iso) => String(iso ?? '').slice(0, 10)
export const msOf = (iso) => Date.parse(iso)

// 웨이퍼가 1장뿐이면 구간 길이를 잡을 이웃이 없다. 축 폭 확보용 렌더링 상수(데이터 아님).
const FALLBACK_SPAN_MS = 4 * 60 * 1000

// catalog.wafers 에 area·equipment_id 를 덧붙인다 (원본에는 chamber_id 만 있다)
export function decorateWafers(wafers) {
  return (wafers ?? []).map((w) => ({
    ...w,
    area: areaOf(w.chamber_id),
    equipment_id: equipmentOf(w.chamber_id),
  }))
}

export const limitMap = (limits) => Object.fromEntries((limits ?? []).map((l) => [l.label, l.value]))

export function judgeOf(min, max, lim) {
  if (min == null || max == null) return null
  if (max > lim.USL || min < lim.LSL) return 'OOS'
  if (max > lim.UCL || min < lim.LCL) return 'OOC'
  return 'OK'
}

// 스텝 표시 순서는 알람의 recipe_step_no 에서 유도한다 (EXPOSE 1 → DEVELOP 2)
export function stepOrderOf(alarms) {
  const order = {}
  for (const a of alarms ?? []) {
    const n = Number(a.recipe_step_no)
    if (!Number.isFinite(n)) continue
    const cur = order[a.recipe_step_name]
    if (cur == null || n < cur) order[a.recipe_step_name] = n
  }
  return order
}

export const sortSteps = (names, order) =>
  [...names].sort((a, b) => (order[a] ?? 99) - (order[b] ?? 99) || a.localeCompare(b))

// 시안 v2: PHO-01-C1 은 PH_DOSE 파라미터도 보유하지만 trace fixture 에 실측이 없다
// (PH_DOSE 알람 0건 → detail 역산 복원 불가). 선택은 가능하게 하고 차트는 골격만 그린다.
// TODO(data): fdc_trace.csv 확보 시 이 보정 제거 — 옵션이 데이터에서 그대로 유도된다
const KNOWN_SENSORS_WITHOUT_TRACE = { 'PHO-01-C1': ['PH_DOSE'] }

/**
 * URL 쿼리스트링(raw)을 실제 선택값으로 정규화한다.
 * 상위 선택이 바뀌면 하위 옵션이 좁혀지고, 유효하지 않은 값은 첫 옵션으로 되돌린다.
 * WAFER 는 비어 있으면 "선택 챔버가 처리한 해당 LOT 의 전 웨이퍼"가 기본값이다.
 */
export function resolveFilters(rows, raw = {}) {
  const all = rows ?? []
  const dates = all.map((r) => dateOf(r.occurred_at)).sort()
  const from = raw.from || dates[0] || ''
  const to = raw.to || dates[dates.length - 1] || ''

  const inRange = all.filter((r) => dateOf(r.occurred_at) >= from && dateOf(r.occurred_at) <= to)
  const pick = (opts, v) => (opts.includes(v) ? v : (opts[0] ?? ''))

  const areas = uniq(inRange.map((r) => r.area)).sort()
  const area = pick(areas, raw.area)
  const inArea = inRange.filter((r) => r.area === area)

  const equipments = uniq(inArea.map((r) => r.equipment_id)).sort()
  const equipment = pick(equipments, raw.equipment)
  const inEquipment = inArea.filter((r) => r.equipment_id === equipment)

  const chambers = uniq(inEquipment.map((r) => r.chamber_id)).sort()
  const chamber = pick(chambers, raw.chamber)
  const inChamber = inEquipment.filter((r) => r.chamber_id === chamber)

  const sensorOpts = uniq([
    ...inChamber.map((r) => r.sensor_id),
    ...(KNOWN_SENSORS_WITHOUT_TRACE[chamber] ?? []),
  ]).sort()
  const askedSensors = (raw.sensors ?? []).filter((s) => sensorOpts.includes(s))
  // 기본값: 챔버가 보유한 전 파라미터
  const sensors = askedSensors.length ? sensorOpts.filter((s) => askedSensors.includes(s)) : sensorOpts
  const inSensor = inChamber.filter((r) => sensors.includes(r.sensor_id))

  const lotOpts = uniq(inSensor.map((r) => r.lot_id)).sort()
  // 기본 LOT 은 가장 최근에 처리된 LOT (진입 직후 최신 구간을 보여준다)
  const latest = inSensor.reduce((best, r) => (!best || msOf(r.occurred_at) > msOf(best.occurred_at) ? r : best), null)
  const lot = lotOpts.includes(raw.lot) ? raw.lot : (latest?.lot_id ?? lotOpts[0] ?? '')
  const inLot = inSensor.filter((r) => r.lot_id === lot)

  const waferOpts = uniq(inLot.map((r) => r.wafer_no)).sort(numAsc)
  const askedWafers = (raw.wafers ?? []).filter((w) => waferOpts.includes(w))
  // 기본값: 전 웨이퍼 (1·3·5 로 줄이지 않는다)
  const wafers = askedWafers.length ? waferOpts.filter((w) => askedWafers.includes(w)) : waferOpts

  return {
    area,
    equipment,
    chamber,
    sensors,
    lot,
    wafers,
    from,
    to,
    options: { areas, equipments, chambers, sensors: sensorOpts, lots: lotOpts, wafers: waferOpts },
  }
}

// 조회 범위에 걸리는 웨이퍼 trace 행
export function selectRows(rows, f, sensor) {
  return (rows ?? [])
    .filter(
      (r) =>
        r.chamber_id === f.chamber &&
        r.lot_id === f.lot &&
        (sensor ? r.sensor_id === sensor : f.sensors.includes(r.sensor_id)) &&
        f.wafers.includes(r.wafer_no) &&
        dateOf(r.occurred_at) >= f.from &&
        dateOf(r.occurred_at) <= f.to,
    )
    .sort((a, b) => msOf(a.occurred_at) - msOf(b.occurred_at) || numAsc(a.wafer_no, b.wafer_no))
}

/**
 * 시간축(measured_at) 시리즈를 만든다.
 *
 * 실측으로 확보된 시각은 웨이퍼 단위 occurred_at 하나뿐이다. 그래서
 *   - 웨이퍼 구간 = [해당 웨이퍼 occurred_at, 다음 웨이퍼 occurred_at)  ← 양끝 모두 실측
 *   - 구간 내부 포인트는 그 구간 안에 균등 배치
 * 로 놓는다. **포인트별 measured_at 실측은 미확보이므로 내부 간격은 렌더링 가정이다.**
 * TODO(data): fdc_trace.csv 의 포인트별 measured_at 연결 시 이 균등 배치를 실측으로 교체
 */
export function buildSeries(rows, stepOrder) {
  const gaps = []
  for (let i = 1; i < rows.length; i += 1) gaps.push(msOf(rows[i].occurred_at) - msOf(rows[i - 1].occurred_at))
  const sortedGaps = [...gaps].sort((a, b) => a - b)
  const span = sortedGaps.length ? sortedGaps[Math.floor(sortedGaps.length / 2)] : FALLBACK_SPAN_MS

  const segments = []
  const points = []
  const missing = []

  rows.forEach((row, idx) => {
    const start = msOf(row.occurred_at)
    const next = rows[idx + 1] ? msOf(rows[idx + 1].occurred_at) : start + span
    const end = next > start ? next : start + span
    const key = `W${row.wafer_no}`
    const steps = sortSteps(Object.keys(row.steps ?? {}), stepOrder)

    const flat = []
    for (const step of steps) {
      const pts = row.steps[step]?.points
      if (Array.isArray(pts)) pts.forEach((v, i) => flat.push({ step, value: v, seq: i + 1, of: pts.length }))
      else missing.push({ waferNo: row.wafer_no, step, mean: row.steps[step]?.mean ?? null })
    }

    flat.forEach((p, i) => {
      points.push({
        ...p,
        key,
        waferNo: row.wafer_no,
        lotHistId: row.lot_hist_id,
        waferAt: row.occurred_at,
        // 균등 배치: 구간을 포인트 수로 나눈 뒤 각 칸의 중앙에 찍는다 (경계선과 겹치지 않게)
        x: start + ((i + 0.5) * (end - start)) / flat.length,
      })
    })

    segments.push({ key, waferNo: row.wafer_no, start, end, at: row.occurred_at, points: flat.length })
  })

  return { segments, points, missing, span }
}

// 알람 시각(occurred_at)을 같은 시각끼리 묶어 세로 점선 1개로 만든다
export function alarmMarkers(alarms) {
  const byTime = new Map()
  for (const a of alarms) {
    const ms = msOf(a.occurred_at)
    if (!Number.isFinite(ms)) continue
    if (!byTime.has(ms)) byTime.set(ms, { ms, at: a.occurred_at, ids: [], judgement: a.judgement, waferNo: a.wafer_no })
    const m = byTime.get(ms)
    m.ids.push(a.alarm_id)
    if (a.judgement === 'OOS') m.judgement = 'OOS'
  }
  return [...byTime.values()].sort((a, b) => a.ms - b.ms)
}

// 조회 범위에 걸린 알람
export function scopedAlarms(alarms, f) {
  return (alarms ?? [])
    .filter(
      (a) =>
        a.chamber_id === f.chamber &&
        a.lot_id === f.lot &&
        f.sensors.includes(a.sensor_id) &&
        f.wafers.includes(a.wafer_no) &&
        dateOf(a.occurred_at) >= f.from &&
        dateOf(a.occurred_at) <= f.to,
    )
    .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at) || a.alarm_id.localeCompare(b.alarm_id))
}

// detail 문자열에서 실측 수치만 뽑는다 (없는 항목은 null)
export function detailNumbers(detail) {
  const grab = (k) => {
    const m = String(detail ?? '').match(new RegExp(`${k}\\s+(-?[0-9.]+)`))
    return m ? Number(m[1]) : null
  }
  return { mean: grab('mean'), min: grab('min'), max: grab('max') }
}

// 알람 카드 한줄 요약 — 수치는 detail 실측값만 쓴다
export function alarmSummary(alarm, lim) {
  const { mean, max } = detailNumbers(alarm.detail)
  if (max != null) {
    const verdict =
      max > lim.USL
        ? `USL ${lim.USL} 초과`
        : max > lim.UCL
          ? `UCL ${lim.UCL} 초과`
          : `${alarm.judgement} · 한계선 내`
    return `${max.toFixed(3)} nm — ${verdict}`
  }
  if (mean != null) return `mean ${mean.toFixed(3)} nm — min·max 실측 미제공`
  return `${alarm.hit_cnt} 웨이퍼 연속 ${alarm.judgement} — 수치 실측 미제공`
}

const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length
// 표본 표준편차 (ddof=1)
const stdSample = (a) => {
  if (a.length < 2) return null
  const m = mean(a)
  return Math.sqrt(a.reduce((s, v) => s + (v - m) ** 2, 0) / (a.length - 1))
}

/**
 * 구간(스텝) 통계 — 선택된 웨이퍼들의 trace 포인트로 클라이언트에서 재집계한다.
 *
 * 선택된 웨이퍼 중 하나라도 그 스텝의 points 를 갖고 있지 않으면(null 이거나 스텝 자체가 없음)
 * 재집계가 성립하지 않는다. 그럴 때만 MEASURED_STEP_STATS 실측값을 찾고, 그것도 없으면 미제공.
 * 키: "LOT|챔버|파라미터|웨이퍼목록" (예: "LOT-260007|PHO-01-C1|PH_FOCUS|1,3,5")
 */
export function stepStats(rows, { lot, chamber, sensor, waferNos, measuredStepStats, stepOrder, lim }) {
  const steps = sortSteps(uniq(rows.flatMap((r) => Object.keys(r.steps ?? {}))), stepOrder)
  const key = `${lot}|${chamber}|${sensor}|${[...waferNos].sort(numAsc).join(',')}`
  const measured = measuredStepStats?.[key] ?? {}

  return steps.map((step) => {
    const complete = rows.length > 0 && rows.every((r) => Array.isArray(r.steps?.[step]?.points))
    if (complete) {
      const values = rows.flatMap((r) => r.steps[step].points)
      const min = Math.min(...values)
      const max = Math.max(...values)
      return {
        step,
        source: 'recomputed',
        mean: mean(values),
        std: stdSample(values),
        min,
        max,
        n: values.length,
        wafers: rows.length,
        judgement: judgeOf(min, max, lim),
      }
    }
    const m = measured[step]
    if (m) {
      return {
        step,
        source: 'measured',
        mean: m.mean,
        std: m.std,
        min: m.min,
        max: m.max,
        n: null,
        wafers: rows.length,
        judgement: judgeOf(m.min, m.max, lim),
      }
    }
    // TODO(data): 포인트 복원 불가 + 지시서 제공 통계도 없음 → 값을 만들지 않는다
    const lacking = rows.filter((r) => !Array.isArray(r.steps?.[step]?.points))
    return {
      step,
      source: 'none',
      mean: null,
      std: null,
      min: null,
      max: null,
      n: null,
      wafers: rows.length,
      judgement: null,
      missingWafers: lacking.map((r) => r.wafer_no),
      noteMeans: lacking.map((r) => r.steps?.[step]?.mean).filter((v) => v != null),
    }
  })
}
