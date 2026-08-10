// 트레이스 뷰어 파생 로직 — 순수 함수만 모아둔 파일 (컴포넌트 없음)
//
// 데이터 출처는 GET /traces/catalog · POST /traces/search · GET /alarms 뿐이다. 값 창작 금지:
// 없는 수치는 null 로 돌려주고 화면에서 "실측 미제공"으로 표기한다.
//
// 한계선은 센서별로 다르다. 전역 상수를 두지 말고 반드시 search 응답의 limits[sensor_id]
// (없으면 catalog.sensors) 를 쓴다 — ET_CF4 에 PH_FOCUS 한계선을 쓰면 판정이 뒤집힌다.

// 설비 prefix → 공정(AREA) 폴백. catalog.equipments 로 못 찾을 때만 쓴다.
const AREA_BY_PREFIX = { PHO: 'PHOTO', ETC: 'ETCH' }

export const areaOf = (chamberId) => AREA_BY_PREFIX[String(chamberId ?? '').slice(0, 3)] ?? '기타'
export const equipmentOf = (chamberId) => String(chamberId ?? '').replace(/-C\d+$/, '')

export const uniq = (arr) => [...new Set(arr)]
export const numAsc = (a, b) => Number(a) - Number(b)
export const dateOf = (iso) => String(iso ?? '').slice(0, 10)
export const msOf = (iso) => Date.parse(iso)

// 웨이퍼가 1장뿐이면 구간 길이를 잡을 이웃이 없다. 축 폭 확보용 렌더링 상수(데이터 아님).
const FALLBACK_SPAN_MS = 4 * 60 * 1000

// TODO(api): catalog.sensors 에 area_id 가 없다 — 파라미터 선택지는 sensor_id prefix 로 공정을 가른다
const AREA_BY_SENSOR_PREFIX = { PH: 'PHOTO', ET: 'ETCH' }
export const areaOfSensor = (sensorId) => AREA_BY_SENSOR_PREFIX[String(sensorId ?? '').slice(0, 2)] ?? null

/**
 * search 응답의 wafers 를 화면용으로 정리한다.
 * - area: catalog.equipments 의 area_id (없으면 챔버 prefix 폴백)
 * - steps: points[] 를 스텝별로 묶은 { [step]: { no, points } } — missing_steps 는 points: null
 */
export function decorateWafers(wafers, catalog) {
  const areaByEqp = Object.fromEntries((catalog?.equipments ?? []).map((e) => [e.equipment_id, e.area_id]))
  return (wafers ?? []).map((w) => {
    const steps = {}
    for (const p of w.points ?? []) {
      steps[p.recipe_step_name] ??= { no: Number(p.recipe_step_no), points: [] }
      steps[p.recipe_step_name].points.push(p)
    }
    // 실측이 없는 스텝 — 화면은 "해당 스텝 실측 미제공"으로 표기한다
    for (const step of w.missing_steps ?? []) steps[step] ??= { no: null, points: null }
    return {
      ...w,
      area: areaByEqp[w.equipment_id] ?? areaOf(w.chamber_id),
      equipment_id: w.equipment_id ?? equipmentOf(w.chamber_id),
      steps,
    }
  })
}

// 센서 한계선 — search 응답 limits[sensor_id] 우선, 없으면 catalog.sensors
export function sensorLimit(limits, catalog, sensorId) {
  return limits?.[sensorId] ?? (catalog?.sensors ?? []).find((s) => s.sensor_id === sensorId) ?? null
}

// 차트 기준선 5개 — 값이 null 인 선은 그리지 않는다 (ET_REFL 은 전부 null → 한계선 미제공)
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

// 포인트 1개의 한계선 판정 — spec 벗어남 OOS · ctrl 벗어남 OOC · 그 외 OK (한계선 없으면 null)
export function judgeValue(value, lim) {
  if (value == null || !lim) return null
  const has = [lim.spec_lower, lim.ctrl_lower, lim.ctrl_upper, lim.spec_upper].some((v) => v != null)
  if (!has) return null
  if (lim.spec_upper != null && value > lim.spec_upper) return 'OOS'
  if (lim.spec_lower != null && value < lim.spec_lower) return 'OOS'
  if (lim.ctrl_upper != null && value > lim.ctrl_upper) return 'OOC'
  if (lim.ctrl_lower != null && value < lim.ctrl_lower) return 'OOC'
  return 'OK'
}

export function judgeOf(min, max, lim) {
  if (min == null || max == null) return null
  const hi = judgeValue(max, lim)
  const lo = judgeValue(min, lim)
  if (hi == null || lo == null) return null
  if (hi === 'OOS' || lo === 'OOS') return 'OOS'
  if (hi === 'OOC' || lo === 'OOC') return 'OOC'
  return 'OK'
}

// 스텝 표시 순서는 recipe_step_no 에서 유도한다 (EXPOSE 1 → DEVELOP 2).
// trace 포인트·알람 어느 쪽을 넣어도 된다 — 둘 다 recipe_step_no/recipe_step_name 을 갖는다.
export function stepOrderOf(items) {
  const order = {}
  for (const it of items ?? []) {
    const n = Number(it.recipe_step_no)
    if (!Number.isFinite(n)) continue
    const cur = order[it.recipe_step_name]
    if (cur == null || n < cur) order[it.recipe_step_name] = n
  }
  return order
}

export const sortSteps = (names, order) =>
  [...names].sort((a, b) => (order[a] ?? 99) - (order[b] ?? 99) || a.localeCompare(b))

/**
 * 조회 요청(POST /traces/search) 파라미터 — catalog 만으로 결정한다.
 * 응답에서 유도해야 하는 값(LOT·WAFER·기간 기본값)은 여기서 채우지 않는다.
 * (응답으로 요청을 만들면 재조회 루프가 생긴다 — 기본값은 resolveFilters 가 표시용으로만 채운다.)
 */
export function resolveScope(catalog, raw = {}) {
  const pick = (opts, v) => (opts.includes(v) ? v : (opts[0] ?? ''))

  const areas = (catalog?.areas ?? []).map((a) => a.area_id)
  const area = pick(areas, raw.area)

  const eqpRows = (catalog?.equipments ?? []).filter((e) => e.area_id === area)
  const equipments = eqpRows.map((e) => e.equipment_id)
  const equipment = pick(equipments, raw.equipment)

  const chambers = eqpRows.find((e) => e.equipment_id === equipment)?.chambers ?? []
  const chamber = pick(chambers, raw.chamber)

  const sensorOpts = (catalog?.sensors ?? [])
    .map((s) => s.sensor_id)
    .filter((id) => areaOfSensor(id) === area || areaOfSensor(id) == null)
  const askedSensors = (raw.sensors ?? []).filter((s) => sensorOpts.includes(s))
  // 기본값: 그 공정의 전 파라미터
  const sensors = askedSensors.length ? sensorOpts.filter((s) => askedSensors.includes(s)) : sensorOpts

  const recipeOpts = (catalog?.recipes ?? []).filter((r) => r.area_id === area).map((r) => r.recipe_id)
  const recipe = pick(recipeOpts, raw.recipe)

  const lotOpts = (catalog?.lots ?? []).map((l) => l.lot_id)

  return {
    area,
    equipment,
    chamber,
    sensors,
    recipe,
    lot: lotOpts.includes(raw.lot) ? raw.lot : '',
    wafers: raw.wafers ?? [],
    from: raw.from ?? '',
    to: raw.to ?? '',
    options: { areas, equipments, chambers, sensors: sensorOpts, recipes: recipeOpts, lots: lotOpts, wafers: [] },
  }
}

/** resolveScope 로 만든 요청 파라미터 → POST /traces/search body */
export function searchBodyOf(scope) {
  return {
    area: scope.area || undefined,
    equipment_id: scope.equipment || undefined,
    chamber_id: scope.chamber || undefined,
    sensor_ids: scope.sensors.length ? scope.sensors : undefined,
    recipe_id: scope.recipe || undefined,
    lot_id: scope.lot || undefined,
    wafer_nos: scope.wafers.length ? scope.wafers.map(Number) : undefined,
    from: scope.from || undefined,
    // 종료일은 그날 자정까지 포함 — 서버 비교 대상은 timestamp 다
    to: scope.to ? `${scope.to} 23:59:59` : undefined,
  }
}

/**
 * 표시용 필터 — catalog 선택지 + 응답(rows)에서 유도하는 기본값(LOT·WAFER·기간)을 합친다.
 * LOT 이 지정되지 않으면 응답에서 가장 최근에 처리된 LOT 을, WAFER 가 비어 있으면 그 LOT 의 전 웨이퍼를 쓴다.
 */
export function resolveFilters(catalog, rows, raw = {}) {
  const scope = resolveScope(catalog, raw)
  const all = rows ?? []

  const dates = all.map((r) => dateOf(r.occurred_at)).sort()
  const from = raw.from || dates[0] || ''
  const to = raw.to || dates[dates.length - 1] || ''

  const inScope = all.filter(
    (r) =>
      r.chamber_id === scope.chamber &&
      scope.sensors.includes(r.sensor_id) &&
      dateOf(r.occurred_at) >= from &&
      dateOf(r.occurred_at) <= to,
  )

  // 기본 LOT 은 가장 최근에 처리된 LOT (진입 직후 최신 구간을 보여준다)
  const latest = inScope.reduce((best, r) => (!best || msOf(r.occurred_at) > msOf(best.occurred_at) ? r : best), null)
  // 조회 결과가 비어 있으면 LOT 을 임의로 고르지 않는다 — 빈 값이면 서버가 전 LOT 을 돌려준다
  const lot = scope.lot || latest?.lot_id || ''
  const inLot = inScope.filter((r) => r.lot_id === lot)

  const waferOpts = uniq(inLot.map((r) => r.wafer_no)).sort(numAsc)
  const askedWafers = (raw.wafers ?? []).map(Number).filter((w) => waferOpts.includes(w))
  // 기본값: 전 웨이퍼 (1·3·5 로 줄이지 않는다)
  const wafers = askedWafers.length ? waferOpts.filter((w) => askedWafers.includes(w)) : waferOpts

  return {
    ...scope,
    lot,
    wafers,
    from,
    to,
    options: { ...scope.options, wafers: waferOpts },
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
 * 포인트 x 는 points[].measured_at 실측 시각이고, 웨이퍼 경계는 occurred_at 이다.
 */
export function buildSeries(rows, stepOrder) {
  const segments = []
  const points = []
  const missing = []

  rows.forEach((row) => {
    const key = `W${row.wafer_no}`
    const start = msOf(row.occurred_at)
    const steps = sortSteps(Object.keys(row.steps ?? {}), stepOrder)

    const flat = []
    for (const step of steps) {
      const pts = row.steps[step]?.points
      if (Array.isArray(pts))
        pts.forEach((p, i) =>
          flat.push({ step, value: p.value, seq: Number(p.seq_no ?? i + 1), of: pts.length, x: msOf(p.measured_at) }),
        )
      else missing.push({ waferNo: row.wafer_no, step })
    }
    flat.sort((a, b) => a.x - b.x)

    for (const p of flat) points.push({ ...p, key, waferNo: row.wafer_no, lotHistId: row.lot_hist_id, waferAt: row.occurred_at })

    const last = flat.length ? flat[flat.length - 1].x : start
    segments.push({ key, waferNo: row.wafer_no, start, end: Math.max(start, last), at: row.occurred_at, points: flat.length })
  })

  return { segments, points, missing, span: FALLBACK_SPAN_MS }
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

/**
 * 알람 카드 한줄 요약 — 수치는 detail 실측값, 이탈 문구는 그 센서의 한계선·단위로 만든다.
 * 상한 초과면 max 를, 하한 미달이면 min 을 쓴다 (ET_CF4 는 sccm 하한 미달로 나와야 한다).
 */
export function alarmSummary(alarm, lim) {
  const { mean, min, max } = detailNumbers(alarm.detail)
  const unit = lim?.unit ? ` ${lim.unit}` : ''
  const say = (v, text) => `${v.toFixed(3)}${unit} — ${text}`

  if (max != null && lim?.spec_upper != null && max > lim.spec_upper) return say(max, `USL ${lim.spec_upper} 초과`)
  if (min != null && lim?.spec_lower != null && min < lim.spec_lower) return say(min, `LSL ${lim.spec_lower} 미달`)
  if (max != null && lim?.ctrl_upper != null && max > lim.ctrl_upper) return say(max, `UCL ${lim.ctrl_upper} 초과`)
  if (min != null && lim?.ctrl_lower != null && min < lim.ctrl_lower) return say(min, `LCL ${lim.ctrl_lower} 미달`)
  if (max != null) return say(max, limitLines(lim).length ? `${alarm.judgement} · 한계선 내` : '한계선 미제공')
  if (mean != null) return `mean ${mean.toFixed(3)}${unit} — min·max 실측 미제공`
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
 * 선택된 웨이퍼 중 하나라도 그 스텝의 포인트를 갖고 있지 않으면 재집계가 성립하지 않는다.
 * 그럴 때만 응답의 measured_step_stats 를 찾고, 그것도 없으면 미제공.
 * 키: "LOT|챔버|파라미터|웨이퍼목록" (예: "LOT-260007|PHO-01-C1|PH_FOCUS|1,3,5")
 */
export function stepStats(rows, { lot, chamber, sensor, waferNos, measuredStepStats, stepOrder, lim }) {
  const steps = sortSteps(uniq(rows.flatMap((r) => Object.keys(r.steps ?? {}))), stepOrder)
  const key = `${lot}|${chamber}|${sensor}|${[...waferNos].sort(numAsc).join(',')}`
  const measured = measuredStepStats?.[key] ?? {}

  return steps.map((step) => {
    const complete = rows.length > 0 && rows.every((r) => Array.isArray(r.steps?.[step]?.points))
    if (complete) {
      const values = rows.flatMap((r) => r.steps[step].points.map((p) => p.value))
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
    // 포인트 실측 없음 + 응답 제공 통계도 없음 → 값을 만들지 않는다
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
    }
  })
}
