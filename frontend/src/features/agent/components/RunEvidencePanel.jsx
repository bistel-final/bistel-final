import RunEvidenceCard, { RunNoData } from './RunEvidenceCard.jsx'
import RunTraceChart from './RunTraceChart.jsx'

// 오버레이 계열 색 — 문제 파라미터(브랜드 블루) 다음으로 teal · navy 순서 고정 배정
const SERIES_COLORS = ['#1E5FC2', '#35B5C8', '#0F2A5C', '#2E7BE8']

const byWaferNo = (a, b) => Number(a.wafer_no) - Number(b.wafer_no)

// 알람 detail 문자열에서 실측 max를 원문 그대로 뽑는다 (반올림·창작 금지)
const parseMax = (detail) => {
  const m = /max\s+([\d.]+)/.exec(detail ?? '')
  return m ? m[1] : null
}

const limitOf = (limits, label) => limits?.find((l) => l.label === label)?.value

// 웨이퍼 trace를 "W{n}·P{i}" 축으로 펼친다. points가 null인 스텝은 건너뛰고 미제공으로 남긴다.
function flatten(wafers, step, key, rowMap) {
  const missing = []
  wafers.forEach((w) => {
    const st = w.steps?.[step] ?? Object.values(w.steps ?? {})[0]
    const pts = st?.points
    if (!Array.isArray(pts)) {
      missing.push(w.wafer_no)
      return
    }
    pts.forEach((v, i) => {
      const x = `W${w.wafer_no}·P${i + 1}`
      const row = rowMap.get(x) ?? { x, __w: Number(w.wafer_no), __p: i }
      row[key] = v
      rowMap.set(x, row)
    })
  })
  return missing
}

const sortRows = (rowMap) => [...rowMap.values()].sort((a, b) => a.__w - b.__w || a.__p - b.__p)

function RunEvidencePanel({ run, runAlarms, allAlarms, catalog }) {
  const inc = run.incident
  const { lot_id: lot, chamber_id: chamber, sensor_id: sensor, recipe_step_name: step } = inc
  const limits = catalog.limits ?? []
  const usl = limitOf(limits, 'USL')

  // ── 1. 문제 파라미터 ──────────────────────────────────────────────
  const incidentWafers = catalog.wafers
    .filter((w) => w.lot_id === lot && w.chamber_id === chamber && w.sensor_id === sensor)
    .sort(byWaferNo)
  const mainMap = new Map()
  const mainMissing = flatten(incidentWafers, step, 'v', mainMap)
  const mainRows = sortRows(mainMap)

  // 「읽는 법」은 알람 실측에서 유도한다 — R03_CONSEC의 hit_cnt(연속 WAFER 수)와
  // 그 연쇄에 포함된 OOS 알람들의 detail max 중 최대값
  const consec = runAlarms.find((a) => a.rule_id === 'R03_CONSEC')
  const oosAlarms = runAlarms.filter((a) => a.rule_id === 'R01_OOS')
  const consecWindow = consec
    ? oosAlarms.filter((a) => a.occurred_at <= consec.occurred_at).slice(-Number(consec.hit_cnt))
    : oosAlarms
  const windowMaxes = consecWindow.map((a) => parseMax(a.detail)).filter(Boolean)
  const peak = windowMaxes.length
    ? windowMaxes.reduce((m, s) => (Number(s) > Number(m) ? s : m), windowMaxes[0])
    : null

  let mainReading
  if (mainRows.length === 0) {
    mainReading = `${lot} · ${chamber} · ${sensor} 의 trace 실측이 없다 — 한계선만 표시한다.`
  } else if (consec && peak) {
    mainReading = `연속 ${consec.hit_cnt} WAFER OOS · 최대 ${peak} (USL ${usl})`
  } else if (peak) {
    mainReading = `OOS ${oosAlarms.length} WAFER · 최대 ${peak} (USL ${usl})`
  } else {
    mainReading = `연속 이탈 판정 실측 미제공 — 한계선(USL ${usl}) 기준으로만 읽는다.`
  }

  // ── 2. 같은 챔버 다른 파라미터 ────────────────────────────────────
  const otherSensors = [
    ...new Set(catalog.wafers.filter((w) => w.chamber_id === chamber && w.sensor_id !== sensor).map((w) => w.sensor_id)),
  ]
  const overlaySensors = [sensor, ...otherSensors]
  const overlayMap = new Map()
  overlaySensors.forEach((sid, i) => {
    const ws = catalog.wafers
      .filter((w) => w.chamber_id === chamber && w.sensor_id === sid && w.lot_id === lot)
      .sort(byWaferNo)
    flatten(ws, step, `s${i}`, overlayMap)
  })
  const overlayRows = otherSensors.length ? sortRows(overlayMap) : []
  const overlaySeries = overlaySensors.map((sid, i) => ({
    key: `s${i}`,
    label: sid,
    color: SERIES_COLORS[i % SERIES_COLORS.length],
    statusDots: i === 0,
    dash: i === 0 ? undefined : '5 4',
  }))

  // ── 3. 정상 WAFER 오버레이 ────────────────────────────────────────
  // 같은 챔버(C1)에는 정상 판정 WAFER가 없어 형제 챔버(C2)를 비교군으로 삼는다
  const sibling = chamber.endsWith('-C1')
    ? chamber.replace(/-C1$/, '-C2')
    : chamber.endsWith('-C2')
      ? chamber.replace(/-C2$/, '-C1')
      : null
  const alarmKey = (lotId, waferNo) => `${lotId}|${waferNo}`
  const siblingAlarmCnt = sibling ? allAlarms.filter((a) => a.chamber_id === sibling).length : 0
  const siblingAlarmed = new Set(
    allAlarms.filter((a) => a.chamber_id === sibling).map((a) => alarmKey(a.lot_id, a.wafer_no)),
  )
  const normalWafers = catalog.wafers
    .filter((w) => w.chamber_id === sibling && w.sensor_id === sensor)
    .filter((w) => !siblingAlarmed.has(alarmKey(w.lot_id, w.wafer_no)))
    .sort(byWaferNo)
  const sameChamberAlarmed = new Set(
    allAlarms.filter((a) => a.chamber_id === chamber).map((a) => alarmKey(a.lot_id, a.wafer_no)),
  )
  const sameChamberNormalCnt = catalog.wafers
    .filter((w) => w.chamber_id === chamber && w.sensor_id === sensor)
    .filter((w) => !sameChamberAlarmed.has(alarmKey(w.lot_id, w.wafer_no))).length

  const normalMap = new Map()
  if (normalWafers.length) {
    flatten(incidentWafers, step, 'bad', normalMap)
    flatten(normalWafers, step, 'good', normalMap)
  }
  const normalRows = normalWafers.length ? sortRows(normalMap) : []
  const normalLabel = `형제 챔버 ${sibling ?? '—'} 정상 WAFER`

  return (
    <>
      <RunEvidenceCard
        index={1}
        title="문제 파라미터"
        meta={`${lot} · ${chamber} · ${sensor} · ${step}`}
        reading={mainReading}
      >
        {mainRows.length === 0 ? (
          <RunNoData label={`${sensor} @ ${chamber}`} note="이 incident의 trace 포인트 실측이 없습니다." />
        ) : (
          <>
            <RunTraceChart
              rows={mainRows}
              series={[{ key: 'v', label: sensor, color: SERIES_COLORS[0], statusDots: true }]}
              limits={limits}
              height={272}
            />
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] font-bold text-slate">
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-oos" />
                OOS (규격 이탈)
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-ooc" />
                OOC (관리한계 초과)
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-brand" />
                정상 범위
              </span>
              <span className="ml-auto font-mono text-slate-light">
                {incidentWafers.length} WAFER · {mainRows.length} 포인트
                {mainMissing.length > 0 && ` · W${mainMissing.join(',')} 포인트 실측 미제공`}
              </span>
            </div>
          </>
        )}
      </RunEvidenceCard>

      <RunEvidenceCard
        index={2}
        title="같은 챔버 다른 파라미터"
        meta={chamber}
        reading="같은 챔버의 다른 파라미터가 함께 흔들렸는지 본다 — 함께 흔들리면 챔버 공통 원인, 이 파라미터만 흔들리면 단일 파라미터 이상이다."
      >
        {/* TODO(data): 이 챔버는 문제 파라미터 외 trace 실측이 없어 오버레이를 그릴 수 없다 */}
        {otherSensors.length === 0 || overlayRows.length === 0 ? (
          <RunNoData
            label={`${chamber} · ${sensor} 외 파라미터`}
            note={`이 챔버의 trace 실측은 ${sensor} 한 종류뿐입니다.`}
          />
        ) : (
          <>
            <RunTraceChart rows={overlayRows} series={overlaySeries} limits={limits} height={240} />
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] font-bold text-slate">
              {overlaySeries.map((s) => (
                <span key={s.key} className="flex items-center gap-1.5">
                  <span className="h-[3px] w-4 rounded-full" style={{ background: s.color }} />
                  <span className="font-mono">{s.label}</span>
                </span>
              ))}
            </div>
          </>
        )}
      </RunEvidenceCard>

      <RunEvidenceCard
        index={3}
        title="정상 WAFER 오버레이"
        meta={normalLabel}
        reading="정상 WAFER 파형과 겹쳐 이탈 폭을 가늠한다 — 비교 실측이 없으면 한계선 기준으로만 이탈 폭을 판단한다."
      >
        {/* TODO(data): 형제 챔버는 알람 0건이라 trace 실측이 없다 — 라벨만 정확히 남긴다 */}
        {normalRows.length === 0 ? (
          <RunNoData
            label={normalLabel}
            note={`${sibling ?? '형제 챔버'} 는 알람 ${siblingAlarmCnt}건이라 trace 실측이 없습니다. 같은 챔버 ${chamber} 의 실측 WAFER는 전량 알람 판정으로 정상 WAFER가 ${sameChamberNormalCnt}건입니다.`}
          />
        ) : (
          <>
            <RunTraceChart
              rows={normalRows}
              series={[
                { key: 'bad', label: `${chamber} 이상 WAFER`, color: SERIES_COLORS[0], statusDots: true },
                { key: 'good', label: normalLabel, color: SERIES_COLORS[1], dash: '5 4' },
              ]}
              limits={limits}
              height={240}
            />
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] font-bold text-slate">
              <span className="flex items-center gap-1.5">
                <span className="h-[3px] w-4 rounded-full bg-brand" />
                {chamber} 이상 WAFER
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-[3px] w-4 rounded-full bg-teal" />
                {normalLabel}
              </span>
            </div>
          </>
        )}
      </RunEvidenceCard>
    </>
  )
}

export default RunEvidencePanel
