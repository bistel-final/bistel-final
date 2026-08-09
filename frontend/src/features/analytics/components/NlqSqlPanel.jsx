// 생성 SQL 패널 — 하이라이팅 / 검증 5항목 / SQL 수정·재검증
// SQL 실행은 읽기 전용 계정 기준, 검증 항목은 POST /analytics/validate 응답을 그대로 렌더한다.

const KEYWORDS = ['SELECT', 'FROM', 'WHERE', 'GROUP', 'BY', 'ORDER', 'DESC', 'COUNT', 'AS', 'LIMIT', 'AND']

const highlightSql = (sql) =>
  String(sql)
    .split('\n')
    .map((line) =>
      line
        .split(/(\s+|,|\(|\)|\*|;|=)/)
        .filter((t) => t !== '')
        .map((t) => {
          if (KEYWORDS.includes(t.toUpperCase())) return { t, c: '#7DD8E5', w: 800 }
          if (/^'.*'$/.test(t)) return { t, c: '#FCD34D', w: 600 }
          if (/^\d+$/.test(t)) return { t, c: '#86EFAC', w: 700 }
          return { t, c: '#E2E8F0', w: 500 }
        }),
    )

// 서버가 label을 주지 않는 경우의 표기 (키 순서 = 검증 순서)
const CHECK_LABELS = {
  single_select: '단일 SELECT 문',
  allowed_tables: '허용 테이블 16종 내',
  columns: '컬럼 검증 통과',
  no_danger: '위험 함수 없음',
  limit: 'LIMIT 500 강제',
}

function NlqSqlPanel({
  sqlText,
  onSqlChange,
  editing,
  onStartEdit,
  onCancelEdit,
  onRun,
  onReverify,
  checks,
  validating,
  verifyNotice,
}) {
  const list = checks ?? []
  const passed = list.filter((c) => c.ok).length

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="flex flex-wrap items-center gap-2.5 border-b border-line bg-page px-[18px] py-3">
        <span className="text-sm font-extrabold text-navy">생성 SQL</span>
        <span className="rounded-md border border-[#BFDBFE] bg-[#F0F6FF] px-2.5 py-[3px] font-mono text-[11.5px] font-extrabold text-brand">
          LLM API 생성
        </span>
        {verifyNotice && (
          <span
            className="animate-[om-fadein_.2s] rounded-md px-2.5 py-[3px] text-xs font-extrabold"
            style={
              verifyNotice.ok
                ? { background: '#DCFCE7', color: '#16A34A' }
                : { background: '#FEE2E2', color: '#DC2626' }
            }
          >
            {verifyNotice.text}
          </span>
        )}
        <div className="ml-auto flex gap-2">
          {editing ? (
            <>
              <button
                onClick={onReverify}
                className="cursor-pointer rounded-[7px] border-none bg-brand px-4 py-[7px] font-sans text-[13px] font-extrabold text-white hover:bg-brand-light"
              >
                재검증
              </button>
              <button
                onClick={onCancelEdit}
                className="cursor-pointer rounded-[7px] border border-line-input bg-white px-4 py-[7px] font-sans text-[13px] font-extrabold text-slate hover:bg-line-soft"
              >
                취소
              </button>
            </>
          ) : (
            <>
              <button
                onClick={onRun}
                className="cursor-pointer rounded-[7px] border-none bg-brand px-4 py-[7px] font-sans text-[13px] font-extrabold text-white hover:bg-brand-light"
              >
                실행
              </button>
              <button
                onClick={onStartEdit}
                className="cursor-pointer rounded-[7px] border border-[#BFDBFE] bg-white px-4 py-[7px] font-sans text-[13px] font-extrabold text-brand hover:bg-[#F0F6FF]"
              >
                SQL 수정 · 재검증
              </button>
            </>
          )}
        </div>
      </div>

      <div className="bg-navy px-5 py-4">
        {editing ? (
          <textarea
            value={sqlText}
            onChange={(e) => onSqlChange(e.target.value)}
            rows={Math.max(3, sqlText.split('\n').length)}
            spellCheck={false}
            className="w-full resize-y rounded-md border border-[#2E4A7E] bg-transparent p-2 font-mono text-sm leading-[1.7] text-[#E2E8F0] focus:border-teal focus:outline-none"
          />
        ) : (
          highlightSql(sqlText).map((toks, i) => (
            <div key={i} className="font-mono text-sm leading-[1.7]">
              {toks.map((tk, j) => (
                <span key={j} className="whitespace-pre" style={{ color: tk.c, fontWeight: tk.w }}>
                  {tk.t}
                </span>
              ))}
            </div>
          ))
        )}
      </div>

      <div className="border-t border-line px-[18px] py-3">
        <div className="mb-2.5 flex items-center gap-2">
          <span className="text-[12.5px] font-extrabold text-navy">SQL 검증</span>
          {validating ? (
            <span className="flex items-center gap-1.5 text-[12px] font-bold text-slate-light">
              <span className="h-3 w-3 animate-[om-spin_.8s_linear_infinite] rounded-full border-2 border-[#D5E2F5] border-t-brand" />
              검증 중...
            </span>
          ) : (
            list.length > 0 && (
              <span
                className="rounded-md px-2 py-[2px] font-mono text-[11.5px] font-extrabold"
                style={
                  passed === list.length
                    ? { background: '#DCFCE7', color: '#16A34A' }
                    : { background: '#FEE2E2', color: '#DC2626' }
                }
              >
                {passed}/{list.length} 통과
              </span>
            )
          )}
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {list.map((c) => (
            <span
              key={c.key}
              className="flex items-center gap-1.5 text-[12.5px] font-bold"
              style={{ color: c.ok ? '#16A34A' : '#DC2626' }}
            >
              <span
                className="flex h-[17px] w-[17px] flex-none items-center justify-center rounded-full text-[10px] font-extrabold"
                style={c.ok ? { background: '#DCFCE7' } : { background: '#FEE2E2' }}
              >
                {c.ok ? '✓' : '✕'}
              </span>
              {c.label ?? CHECK_LABELS[c.key] ?? c.key}
            </span>
          ))}
        </div>
      </div>

      <div className="border-t border-line bg-page px-[18px] py-[9px] font-mono text-[12.5px] font-bold text-slate">
        읽기 전용 계정(kosa_readonly)으로 실행 · LIMIT 500 · timeout 5s
      </div>
    </div>
  )
}

export default NlqSqlPanel
