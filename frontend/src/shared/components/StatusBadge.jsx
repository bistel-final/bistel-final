// 상태 뱃지 — 연한 배경 + 진한 글자 (dc.html 색 조합)
const TONES = {
  oos: { bg: '#FEE2E2', color: '#DC2626' }, // OOS · R01 · 실패
  ooc: { bg: '#FEF3C7', color: '#D97706' }, // OOC · R02 · 대기
  ok: { bg: '#DCFCE7', color: '#16A34A' }, // 완료 · 성공
  neutral: { bg: '#F1F5F9', color: '#475569' }, // 미처리
  muted: { bg: '#F1F5F9', color: '#64748B' }, // 취소 · 스킵
  info: { bg: '#EDF2FA', color: '#1E5FC2' },
  blue: { bg: '#DBEAFE', color: '#1E5FC2' },
  critical: { bg: '#DC2626', color: '#FFFFFF' }, // R03_CONSEC (진한 배경 예외)
  navy: { bg: '#0F2A5C', color: '#FFFFFF' }, // 조치 코드
}

function StatusBadge({ tone = 'neutral', mono = false, className = '', style, children }) {
  const t = TONES[tone] ?? TONES.neutral
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-[3px] text-[11.5px] font-extrabold ${mono ? 'font-mono' : ''} ${className}`}
      style={{ background: t.bg, color: t.color, ...style }}
    >
      {children}
    </span>
  )
}

export default StatusBadge
