import {
  COMPARISON_LABELS, COMPARISON_STATES, ORIGIN_LABELS, excursionPercent,
} from '../investigation-view.js'

const DIRECTIONS = { ABOVE: '상한 초과', BELOW: '하한 미달', BOTH: '양방향 이탈' }
const WAFER_SCOPE = { SINGLE: '단일', PARTIAL: '일부', ALL: '관찰 대상 전체' }

export default function RunInvestigationCard({ diagnosis }) {
  if (diagnosis?.status !== 'AVAILABLE' || !diagnosis.origin_assessment) return null
  const findings = diagnosis.parameter_findings ?? []
  const origin = diagnosis.origin_assessment
  return (
    <section className="rounded-xl border border-line bg-white p-5" aria-label="파라미터 판정과 조사 범위">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-extrabold text-navy">파라미터 판정 · 조사 범위</h2>
        <span className="text-sm text-g1">소재 판정: {ORIGIN_LABELS[origin.scope] ?? '미확정'}</span>
      </div>
      {findings.length ? (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="mb-2 text-left text-xs text-g1">초과율은 이탈 크기를 목표값부터 관리한계까지의 폭으로 나눈 값입니다.</caption>
            <thead className="border-b border-line text-g1"><tr>
              <th className="py-2">파라미터</th><th>Recipe Step</th><th>방향</th><th>초과율</th><th>Wafer 범위</th>
            </tr></thead>
            <tbody>{findings.map((finding) => (
              <tr className="border-b border-line last:border-0" key={`${finding.parameter_id}:${finding.step_no}`}>
                <td className="py-2 font-mono">{finding.parameter_id}</td>
                <td>{finding.step_no}</td><td>{DIRECTIONS[finding.direction]}</td>
                <td>{excursionPercent(finding.excursion_ratio)}</td><td>{WAFER_SCOPE[finding.wafer_scope]}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : <p className="mt-3 text-sm text-g1">유효한 파라미터 이탈 근거가 없습니다.</p>}
      <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-5" aria-label="확인 매트릭스">
        {Object.entries(COMPARISON_LABELS).map(([key, label]) => {
          const status = origin.compared[key]
          return <div key={key} className="rounded-lg border border-line p-3">
            <dt className="text-xs text-g1">{label}</dt>
            <dd className={`mt-1 text-sm font-semibold ${status === 'CHECKED' ? 'text-navy' : 'text-g1'}`}>
              {COMPARISON_STATES[status] ?? '상태 미제공'}
            </dd>
          </div>
        })}
      </dl>
      {origin.basis.length > 0 && <p className="mt-3 break-words text-xs text-g1">
        소재 판정 근거: {origin.basis.map((ref) => `${ref.namespace}: ${ref.id}`).join(' · ')}
      </p>}
    </section>
  )
}
