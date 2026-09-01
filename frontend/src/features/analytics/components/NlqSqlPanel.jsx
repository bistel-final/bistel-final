// 생성 SQL — 발표 재설계: SQL 은 실제 줄 수만큼만, 검증 6종은 가로 스트립 + 통과 배지 하나.
// 검증 항목은 POST /analytics/validate 응답(checks)을 그대로 렌더하고,
// valid=false 면 같은 응답의 reason 을 그대로 노출한다.
// "SQL 수정"을 누르면 pre 대신 textarea 로 전환해 편집 후 재검증·실행한다 (passthrough 경로, 검증 포함).
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'

// 서버 CHECK_KEYS(6종) 표기 — 키 순서 = 검증 순서. 서버 label 은 폴백.
const CHECK_LABELS = {
  single_select: '단일 SELECT',
  allowed_objects: '허용 객체',
  column_allowlist: '컬럼 allowlist',
  no_catalog_access: '카탈로그 차단',
  no_dangerous_function: '함수 allowlist',
  limit_enforced: 'LIMIT 500',
}

function CheckChip({ ok, children }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] font-semibold ${
        ok ? 'border-tint-green-line bg-tint-green text-green' : 'border-tint-red-line bg-tint-red text-red'
      }`}
    >
      <span className="font-mono text-[11px]">{ok ? '✓' : '✕'}</span>
      {children}
    </span>
  )
}

function NlqSqlPanel({
  sqlText,
  onSqlChange,
  editing,
  onStartEdit,
  onCancelEdit,
  onReverify,
  onRunEdited,
  checks,
  valid,
  reason,
  validating,
  verifyNotice,
}) {
  const list = checks ?? []
  const passed = list.filter((c) => c.ok).length
  const failReason = valid === false && reason ? reason : null
  const lines = Math.max(2, sqlText.split('\n').length)

  return (
    <Card className="px-6 pb-5 pt-4">
      <div className="mb-3 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="text-[15px] font-bold tracking-[-.01em] text-ink">생성 SQL</span>
          {validating ? (
            <span className="flex items-center gap-2 text-[12px] text-g1">
              <span className="h-3.5 w-3.5 animate-[om-spin_.8s_linear_infinite] rounded-full border-2 border-tint-blue border-t-blue" />
              검증 중…
            </span>
          ) : list.length > 0 ? (
            <Badge variant={valid === false ? 't-red' : 't-green'}>
              {valid === false ? `검증 실패 · ${passed}/${list.length}` : `검증 통과 · ${passed}/${list.length}`}
            </Badge>
          ) : null}
          {verifyNotice && <Badge variant={verifyNotice.ok ? 't-green' : 't-red'}>{verifyNotice.text}</Badge>}
        </div>
        <div className="flex gap-2">
          {editing ? (
            <>
              <Button sm onClick={onRunEdited}>
                검증 후 실행
              </Button>
              <Button sm variant="outline" onClick={onReverify}>
                재검증만
              </Button>
              <Button sm variant="outline" onClick={onCancelEdit}>
                취소
              </Button>
            </>
          ) : (
            <Button sm variant="outline" onClick={onStartEdit}>
              SQL 수정
            </Button>
          )}
        </div>
      </div>

      {editing ? (
        <textarea
          value={sqlText}
          onChange={(e) => onSqlChange(e.target.value)}
          rows={Math.max(4, lines)}
          spellCheck={false}
          aria-label="SQL 편집"
          className="w-full resize-y rounded-lg border border-blue bg-white px-5 py-3.5 font-mono text-[13px] leading-[1.75] text-ink focus:outline-none"
        />
      ) : (
        <pre className="overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-soft px-5 py-3.5 font-mono text-[13px] leading-[1.75] text-ink">
          {sqlText}
        </pre>
      )}

      {/* 검증 스트립 — 6종이 한 줄로 읽힌다: "SQL 이 어떤 관문을 지났는가" */}
      {list.length > 0 && !validating && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {list.map((c) => (
            <CheckChip key={c.key} ok={c.ok}>
              {CHECK_LABELS[c.key] ?? c.label ?? c.key}
            </CheckChip>
          ))}
        </div>
      )}
      {failReason && (
        <div className="mt-3 rounded-md border border-tint-red-line bg-tint-red px-3.5 py-2.5 font-mono text-[12px] font-semibold leading-relaxed text-red">
          {failReason}
        </div>
      )}
    </Card>
  )
}

export default NlqSqlPanel
