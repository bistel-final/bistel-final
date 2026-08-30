export const auditTargetsOf = ({ agent_run_id, action_id, approval_id }) =>
  [
    ['AGENT_RUN', agent_run_id],
    ['ACTION', action_id],
    ['APPROVAL', approval_id],
  ].filter(([, id]) => Boolean(id))

export const mergeAuditItems = (groups) => {
  const byId = new Map()
  for (const item of groups.flat()) byId.set(item.audit_id, item)
  return [...byId.values()].sort(
    (a, b) =>
      String(b.occurred_at).localeCompare(String(a.occurred_at)) ||
      Number(b.audit_id) - Number(a.audit_id),
  )
}
