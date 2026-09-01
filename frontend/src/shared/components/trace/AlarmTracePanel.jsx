import { HistoryTrendCard } from './HistoryTrendChart.jsx'

/** 화면 2와 Agent가 함께 쓰는 incident 단위 Trace 패널. */
export default function AlarmTracePanel(props) {
  return <HistoryTrendCard {...props} />
}
