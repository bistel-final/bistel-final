import StatusBadge from '../../../shared/components/StatusBadge.jsx'

const DASH = '—'

// 이 화면에서 노출하는 Tool은 3종. send_action은 승인 후 전송 단계에서 실행되므로 목록에 없고,
// decide_action·classify_fault는 Tool이 아니라 그래프 노드이므로 아래 「노드」 구분선에서 따로 표기한다.
const SHOWN_TOOLS = ['get_fdc_summary', 'get_equipment_context', 'search_documents']

const TOOL_DESC = {
  get_fdc_summary: 'incident 알람·trace 요약 집계',
  get_equipment_context: '설비·챔버·상하류 관계 조회',
  search_documents: '장비 스펙·트러블 가이드 문서 검색',
}

const NODE_DESC = {
  classify_fault: 'fault 코드·원인 분류',
  decide_action: '조치 코드·심각도 결정',
}

function RunToolCallsCard({ toolCalls, nodes }) {
  const tools = (toolCalls ?? []).filter((t) => SHOWN_TOOLS.includes(t.name))

  return (
    <div className="rounded-xl border border-line bg-white px-[18px] py-4 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-sm font-extrabold text-navy">Tool 호출</span>
        <span className="font-mono text-[11.5px] font-bold text-slate-light">{tools.length}건</span>
        <span className="ml-auto text-[11.5px] font-bold text-slate-light">LLM API가 선택해 실행</span>
      </div>

      <div className="flex flex-col">
        {tools.length === 0 && <span className="text-[12.5px] font-semibold text-slate">실측 미제공</span>}
        {tools.map((t) => (
          <div key={t.name} className="flex items-center gap-2.5 border-b border-line-soft py-2 last:border-b-0">
            <span className="flex h-[19px] w-[19px] flex-none items-center justify-center rounded-md bg-line-soft font-mono text-[11px] font-extrabold text-brand">
              {t.seq}
            </span>
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="font-mono text-[12.5px] font-extrabold text-ink">{t.name}</span>
              <span className="text-[11.5px] font-semibold text-slate-light">{TOOL_DESC[t.name] ?? ''}</span>
            </span>
            <StatusBadge tone={t.status === 'OK' ? 'ok' : 'oos'} mono className="ml-auto">
              {t.status}
            </StatusBadge>
            <span className="w-[52px] text-right font-mono text-[12px] font-bold text-slate">
              {typeof t.ms === 'number' ? `${t.ms}ms` : DASH}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-3.5 flex items-center gap-2.5">
        <span className="h-px flex-1 bg-line" />
        <span className="text-[11.5px] font-extrabold text-slate-light">노드</span>
        <span className="h-px flex-1 bg-line" />
      </div>

      <div className="mt-1.5 flex flex-col">
        {(nodes ?? []).length === 0 && <span className="text-[12.5px] font-semibold text-slate">실측 미제공</span>}
        {(nodes ?? []).map((n) => (
          <div key={n.name} className="flex items-center gap-2.5 border-b border-line-soft py-2 last:border-b-0">
            <span className="flex h-[19px] w-[19px] flex-none items-center justify-center rounded-md bg-navy font-mono text-[11px] font-extrabold text-white">
              {n.seq}
            </span>
            <span className="font-mono text-[12.5px] font-extrabold text-ink">{n.name}</span>
            <span className="text-[11.5px] font-semibold text-slate-light">{NODE_DESC[n.name] ?? ''}</span>
            <StatusBadge tone="info" mono className="ml-auto">
              NODE
            </StatusBadge>
          </div>
        ))}
      </div>

      <div className="mt-2.5 text-[11.5px] font-semibold leading-[1.5] text-slate-light">
        노드는 그래프 실행 단계이지 Tool이 아닙니다 — Tool 목록과 섞어 세지 않습니다.
      </div>
    </div>
  )
}

export default RunToolCallsCard
