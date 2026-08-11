import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'

const DASH = '—'

function JsonBlock({ value }) {
  return (
    <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-soft p-3 font-mono text-[11px] leading-5 text-ink">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

<<<<<<< Updated upstream
function RunEvidencePanel({ run, runAlarms, allAlarms, trace, equipmentId }) {
  // incident 는 (lot_id, chamber_id) 두 개뿐 — sensor_id·recipe_step_name 은 run 의 형제 필드
  const { lot_id: lot, chamber_id: chamber } = run.incident ?? {}
  const sensor = run.sensor_id
  const step = run.recipe_step_name
  // 한계선은 센서별로 다르다 — POST /traces/search 응답의 limits[sensor_id] 에서 읽는다
  const usl = trace?.limits?.[sensor]?.spec_upper ?? null

  // ── ① 문제 파라미터 — trace 응답(웨이퍼 오름차순 × seq_no 순서)에서 좌표 변환 ──
  const values = (trace?.wafers ?? [])
    .filter((w) => w.lot_id === lot && w.chamber_id === chamber && w.sensor_id === sensor)
    .sort((a, b) => Number(a.wafer_no) - Number(b.wafer_no))
    .flatMap((w) =>
      [...(w.points ?? [])]
        .sort((a, b) => a.seq_no - b.seq_no)
        .filter((p) => p.recipe_step_name === step)
        .map((p) => p.value),
    )

  // 읽는 법은 알람 실측에서 유도 — R03_CONSEC hit_cnt + 연쇄 창 안 OOS detail max 의 최대값
  const consec = runAlarms.find((a) => a.rule_id === 'R03_CONSEC') ?? null
  const oosAlarms = runAlarms.filter((a) => a.rule_id === 'R01_OOS')
  const consecWindow = consec
    ? oosAlarms.filter((a) => a.occurred_at <= consec.occurred_at).slice(-Number(consec.hit_cnt))
    : oosAlarms
  const maxes = consecWindow.map((a) => parseMax(a.detail)).filter(Boolean)
  const peak = maxes.length ? maxes.reduce((m, s) => (Number(s) > Number(m) ? s : m), maxes[0]) : null
  const oocCnt = runAlarms.filter((a) => a.judgement === 'OOC').length
  // 한계선 미제공 센서(ET_REFL)는 USL 을 창작하지 않고 빼고 쓴다
  const uslText = typeof usl === 'number' ? ` (USL ${usl})` : ''
  const read1 =
    consec && peak
      ? `연속 ${consec.hit_cnt} WAFER OOS · 최대 ${peak}${uslText}`
      : peak
        ? `OOS ${oosAlarms.length} WAFER · 최대 ${peak}${uslText}`
        : `OOS 없음 · OOC ${oocCnt}건 — 한계선 이탈 실측 미제공`

  // ── ② 같은 챔버 다른 파라미터 — 같은 챔버 나머지 감시 파라미터의 알람 유무 ──
  const others = (PARAMS_BY_PREFIX[String(sensor ?? '').slice(0, 2)] ?? []).filter((s) => s !== sensor)
  const otherAlarmCnt = allAlarms.filter((a) => a.chamber_id === chamber && others.includes(a.sensor_id)).length
  const read2 =
    others.length === 0
      ? '감시 파라미터 목록 실측 미제공'
      : otherAlarmCnt === 0
        ? `나머지 ${others.length} 종은 전 구간 IN_CONTROL — 이 파라미터만의 문제`
        : `나머지 ${others.length} 종에서 알람 ${otherAlarmCnt}건 — 챔버 공통 원인 의심`

  // ── ③ 형제 챔버 정상 WAFER — C1↔C2 를 비교군으로 삼는다 ──
  const chamberId = String(chamber ?? '')
  const sibling = /-C1$/.test(chamberId)
    ? chamberId.replace(/-C1$/, '-C2')
    : /-C2$/.test(chamberId)
      ? chamberId.replace(/-C2$/, '-C1')
      : null
  const oosCnt = runAlarms.filter((a) => a.judgement === 'OOS').length
  // OOS = 규격(USL/LSL) 밖 — 정상 밴드 이탈은 OOS 존재로만 판단하고, 없으면 창작하지 않는다
  const read3 = oosCnt > 0 ? '평소 밴드를 크게 벗어남' : `OOS 알람 없음 — 밴드 이탈 폭 실측 미제공`

  // ── ④ 상류 PHOTO vs 하류 ETCH — incident 반대편 설비의 같은 LOT 알람 유무 ──
  const isUpstream = equipmentId === FLOW.upstream
  const counterpart = isUpstream ? FLOW.downstream : FLOW.upstream
  const counterLabel = isUpstream ? '하류' : '상류'
  const propagated = allAlarms.filter((a) => a.lot_id === lot && a.equipment_id === counterpart).length
  const read4 = !equipmentId
    ? '설비 관계 실측 미제공'
    : propagated === 0
      ? `${counterLabel} ${counterpart} 은 전 구간 정상 — 독립 이상. 하향 대상 아님`
      : `${counterLabel} ${counterpart} 알람 ${propagated}건 — 연쇄 의심`
  const tag4 = equipmentId && propagated === 0 ? '하향 판정 근거' : null

  // ── ⑤ 계측 CD 결과 — incident LOT(가능하면 같은 WAFER)의 CD FAIL 실측만 인정 ──
  const waferSet = new Set(runAlarms.map((a) => String(a.wafer_no)))
  const lotFails = METROLOGY_FAILS.filter((m) => m.lot === lot)
  const waferFails = lotFails.filter((m) => waferSet.has(String(m.wafer)))
  const params5 = [...new Set((waferFails.length ? waferFails : lotFails).map((m) => m.param))].join(' · ')
  const read5 = waferFails.length
    ? `같은 WAFER ${params5} FAIL — 품질 영향 확인됨`
    : lotFails.length
      ? `같은 LOT ${params5} FAIL ${lotFails.length}건 — 품질 영향 확인됨`
      : `incident LOT ${lot} 의 CD 계측 FAIL 실측 미제공`
  const tag5 = lotFails.length ? '상향 판정 근거' : null
=======
function EvidenceSection({ title, note, children }) {
  return (
    <section className="rounded-lg border border-line bg-white p-4">
      <div className="mb-2 text-[13px] font-extrabold text-navy">{title}</div>
      {note && <div className="mb-3 text-xs leading-5 text-g1">{note}</div>}
      {children}
    </section>
  )
}

function RunEvidencePanel({ run }) {
  const evidence = run.evidence ?? {}
  const documents = evidence.document_hits ?? []
  const upstream = evidence.upstream ?? []
  const errors = evidence.errors ?? []
>>>>>>> Stashed changes

  return (
    <Card className="min-w-0 flex-1">
      <CardHeader title="판단 근거" note="Agent 실행 응답에 저장된 근거만 표시" />
      <div className="grid grid-cols-1 gap-3.5 px-5 pb-5 xl:grid-cols-2">
        <EvidenceSection title="대표 FDC 요약" note={run.representative_alarm_id ?? DASH}>
          {evidence.representative_fdc ? <JsonBlock value={evidence.representative_fdc} /> : <EmptyState title="대표 FDC 근거 없음" />}
        </EvidenceSection>

        <EvidenceSection title="R03 연속 이상 근거">
          {evidence.r03_fdc || run.r03_fdc_evidence ? (
            <JsonBlock value={evidence.r03_fdc ?? run.r03_fdc_evidence} />
          ) : (
            <EmptyState title="R03 근거 없음" description="해당 incident에 R03_CONSEC 근거가 없습니다." />
          )}
        </EvidenceSection>

        <EvidenceSection title="설비 관계" note="Neo4j 관계 조회 Tool 결과">
          {evidence.equipment_context ? <JsonBlock value={evidence.equipment_context} /> : <EmptyState title="관계 근거 없음" />}
        </EvidenceSection>

        <EvidenceSection title="상류 기여 근거" note={`${upstream.length}건`}>
          {upstream.length ? <JsonBlock value={upstream} /> : <EmptyState title="상류 근거 없음" />}
        </EvidenceSection>

        <EvidenceSection title="문서 검색 근거" note={`${documents.length}건`}>
          {documents.length ? (
            <div className="flex flex-col gap-2">
              {documents.map((document, index) => (
                <div key={document.chunk_id ?? `${document.document_id}-${index}`} className="rounded-lg bg-soft p-3">
                  <div className="text-xs font-bold text-navy">{document.title ?? document.document_id ?? DASH}</div>
                  <div className="mt-1 line-clamp-3 text-[11.5px] leading-5 text-g1">
                    {document.content ?? DASH}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="문서 근거 없음" />
          )}
        </EvidenceSection>

        <EvidenceSection title="부분 실패" note={`${errors.length}건`}>
          {errors.length ? <JsonBlock value={errors} /> : <EmptyState title="Tool 오류 없음" />}
        </EvidenceSection>
      </div>
    </Card>
  )
}

export default RunEvidencePanel
