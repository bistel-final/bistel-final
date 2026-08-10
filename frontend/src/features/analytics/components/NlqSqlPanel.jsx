// 생성 SQL — 좌측 SQL 원문(플레인 pre) + 우측 230px 검증 5항목 (디자인 v2 06)
// 검증 항목은 POST /analytics/validate 응답(checks)을 그대로 렌더하고,
// valid=false 면 같은 응답의 reason 을 그대로 노출한다.
// "SQL 수정 · 재검증"을 누르면 pre 대신 textarea로 전환해 편집 후 재검증한다.
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import CheckRow from '../../../shared/components/ui/CheckRow.jsx'

// 시안 표기 (키 순서 = 검증 순서). 서버 label은 폴백으로만 사용.
const CHECK_LABELS = {
  single_select: '단일 SELECT',
  allowed_tables: '허용 테이블',
  columns: '컬럼 검증',
  no_danger: '위험 함수 없음',
  limit: 'LIMIT 500 강제',
}

function NlqSqlPanel({
  sqlText,
  onSqlChange,
  editing,
  onStartEdit,
  onCancelEdit,
  onReverify,
  checks,
  valid,
  reason,
  validating,
  verifyNotice,
}) {
  const list = checks ?? []
  const failReason = valid === false && reason ? reason : null

  return (
    <Card>
      <CardHeader title="생성 SQL" note="sqlglot 검증 통과 · 실행 전 확인" />
      <div className="flex items-stretch gap-[18px] px-5 pb-[18px]">
        {editing ? (
          <textarea
            value={sqlText}
            onChange={(e) => onSqlChange(e.target.value)}
            rows={Math.max(5, sqlText.split('\n').length)}
            spellCheck={false}
            aria-label="SQL 편집"
            className="min-w-0 flex-1 resize-y rounded-lg border border-line bg-soft px-[18px] py-4 font-mono text-xs leading-[1.7] text-ink focus:border-blue focus:outline-none"
          />
        ) : (
          <pre className="min-w-0 flex-1 overflow-auto rounded-lg border border-line bg-soft px-[18px] py-4 font-mono text-xs leading-[1.7] text-ink">
            {sqlText}
          </pre>
        )}
        <div className="flex w-[230px] flex-none flex-col gap-2.5">
          {validating ? (
            <span className="flex items-center gap-2 text-xs text-g1">
              <span className="h-3.5 w-3.5 animate-[om-spin_.8s_linear_infinite] rounded-full border-2 border-tint-blue border-t-blue" />
              검증 중…
            </span>
          ) : (
            list.map((c) => (
              <CheckRow key={c.key} ok={c.ok}>
                {CHECK_LABELS[c.key] ?? c.label ?? c.key}
              </CheckRow>
            ))
          )}
          {failReason && (
            <span className="rounded-md border border-tint-red-line bg-tint-red px-2.5 py-1.5 font-mono text-[11px] font-semibold text-red">
              {failReason}
            </span>
          )}
          {verifyNotice && (
            <Badge variant={verifyNotice.ok ? 't-green' : 't-red'} className="self-start">
              {verifyNotice.text}
            </Badge>
          )}
          {editing ? (
            <div className="mt-auto flex gap-2">
              <Button sm onClick={onReverify} className="flex-1">
                재검증
              </Button>
              <Button sm variant="outline" onClick={onCancelEdit} className="flex-1">
                취소
              </Button>
            </div>
          ) : (
            <Button sm variant="outline" onClick={onStartEdit} className="mt-auto">
              SQL 수정 · 재검증
            </Button>
          )}
        </div>
      </div>
    </Card>
  )
}

export default NlqSqlPanel
