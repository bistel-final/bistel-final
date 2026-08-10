// 판정 섹션 — fault 코드·이름·원인·confidence 를 t-red 톤 박스에 묶는다 (시안 04)
// fault 룩업 상수는 없다 — 응답의 fault_code · fault_name · fault_name_en · cause_summary 를 그대로 쓴다
function RunVerdictCard({ run }) {
  const confidence = typeof run.confidence === 'number' ? run.confidence.toFixed(2) : null

  return (
    <>
      <div className="text-[15px] font-extrabold text-navy">판정</div>
      <div className="rounded-lg border border-tint-red-line bg-row-red p-4">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-2xl font-extrabold text-red">{run.fault_code || '—'}</span>
          <span>
            <span className="block text-[14.5px] font-extrabold text-ink">{run.fault_name ?? '실측 미제공'}</span>
            <span className="mt-0.5 block font-mono text-[10px] text-g1">{run.fault_name_en ?? ''}</span>
          </span>
        </div>
        <div className="mt-3 text-[12.5px] text-ink">{run.cause_summary ?? '원인 문구 실측 미제공'}</div>
        <div className="mt-1.5 font-mono text-[11px] text-g1">
          {confidence === null ? 'confidence 실측 미제공' : `confidence ${confidence}`}
        </div>
      </div>
    </>
  )
}

export default RunVerdictCard
