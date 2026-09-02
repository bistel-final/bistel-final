import EmptyState from '../../../shared/components/EmptyState.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'

const percent = (value) => value == null ? '—' : `${(value * 100).toFixed(1)}%`
const countMetric = (metric) => metric ? `${metric.numerator}/${metric.denominator} · ${percent(metric.rate)}` : '—'
const metricText = (value) => typeof value === 'object' ? JSON.stringify(value) : String(value)

function MetricCard({ label, value }) {
  return <div className="rounded-lg border border-cell-line bg-soft px-3 py-2"><div className="text-[10px] font-bold text-g2">{label}</div><div className="mt-1 font-mono text-[12px] font-extrabold text-ink">{value}</div></div>
}

export default function AgentEvaluationPanel({ evaluation }) {
  if (!evaluation) return <EmptyState title="평가 결과를 불러오지 못했습니다" />
  const fault = evaluation.fault_5class
  const golden = evaluation.golden_flow
  return (
    <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] gap-4">
      <Card>
        <CardHeader title="합성 Fault 5-class 평가" note="평가 전용 공개 합성 라벨" />
        <div className="px-5 pb-5">
          {!fault ? <EmptyState title="Fault 평가 artifact 없음" description={evaluation.fault_5class_empty_reason} /> : <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={fault.hard_gate_passed ? 't-green' : 't-red'}>{fault.hard_gate_passed ? 'HARD GATE PASS' : 'HARD GATE FAIL'}</Badge>
              <Badge variant="t-blue">{fault.label_source}</Badge>
              <Badge variant="t-gray">{fault.usage_scope}</Badge>
              <span className="font-mono text-[10.5px] text-g2">{fault.versions.dataset_epoch}</span>
            </div>
            <div className="mt-3 font-mono text-[10.5px] text-g2">
              model {fault.versions.model_version ?? '—'} · prompt {fault.versions.prompt_version ?? '—'} · policy {fault.versions.policy_version ?? '—'}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2">
              <MetricCard label="구조화 판단" value={countMetric(fault.structured_prediction)} />
              <MetricCard label="근거 유효 run" value={countMetric(fault.evidence_valid_run)} />
              <MetricCard label="규칙 조치 일치" value={countMetric(fault.rule_action_agreement)} />
              <MetricCard label="5-class 분류 정확도" value={countMetric(fault.classification.accuracy)} />
              <MetricCard label="고정 5-class Macro-F1" value={percent(fault.classification.macro_f1_5class)} />
              <MetricCard label="분류/미분류" value={`${fault.classification.population_count}/${fault.classification.unclassified_count}`} />
            </div>
            <div className="mt-4 overflow-hidden rounded-lg border border-line">
              <table className="w-full text-[10.5px]">
                <thead className="bg-soft text-g2"><tr><th className="px-2 py-1.5 text-left">Class</th><th>Support</th><th>P</th><th>R</th><th>F1</th></tr></thead>
                <tbody>{Object.entries(fault.classification.by_class).map(([name, metric]) => <tr key={name} className="border-t border-cell-line text-center"><td className="px-2 py-1.5 text-left font-mono font-bold">{name}</td><td>{metric.support}</td><td>{percent(metric.precision)}</td><td>{percent(metric.recall)}</td><td>{percent(metric.f1)}</td></tr>)}</tbody>
              </table>
            </div>
            <div className="mt-3 space-y-1 text-[11px] text-g1">
              {fault.exclusions.map((item) => <div key={item.reason}><strong>{item.reason} {item.count}건</strong> · {item.meaning}</div>)}
              <div>Metrology 관측 {fault.metrology_observed_count}/{fault.metrology_total_lot_hist_count}</div>
              {!fault.hard_gate_passed && fault.hard_gate_reasons.map((reason) => <div key={reason} className="text-red">Gate · {reason}</div>)}
            </div>
            <div className="mt-4 rounded-lg border border-tint-amber-line bg-tint-amber px-3 py-2 text-[11px] text-tint-amber-text">{fault.production_performance_disclaimer}</div>
          </>}
        </div>
      </Card>
      <Card>
        <CardHeader title="Golden flow 7단 검증" note="immutable summary" />
        <div className="px-5 pb-5">
          {!golden ? <EmptyState title="Golden-flow artifact 없음" description={evaluation.golden_flow_empty_reason} /> : <>
            <Badge variant={golden.status === 'PASS' ? 't-green' : 't-red'}>{golden.status}</Badge>
            <div className="mt-3 flex flex-col gap-1.5">{golden.phases.map((phase, index) => <div key={phase.phase} className="rounded-lg border border-line bg-soft px-3 py-2"><div className="flex items-center justify-between"><span className="font-mono text-[11px] font-bold">{index + 1}. {phase.phase}</span><Badge variant={phase.status === 'PASS' ? 't-green' : phase.status === 'FAIL' ? 't-red' : 't-amber'}>{phase.status}</Badge></div>{Object.keys(phase.metrics).length > 0 && <div className="mt-1 font-mono text-[9.5px] text-g2">{Object.entries(phase.metrics).map(([key, value]) => `${key}=${metricText(value)}`).join(' · ')}</div>}{phase.reasons.map((reason) => <div key={reason} className="mt-1 text-[10.5px] text-red">{reason}</div>)}</div>)}</div>
          </>}
        </div>
      </Card>
    </div>
  )
}
