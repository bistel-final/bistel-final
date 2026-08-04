import { useParams } from 'react-router-dom'
import PagePlaceholder from '../../../shared/components/states/PagePlaceholder.jsx'

function TracePage() {
  const { lotHistId } = useParams()

  return (
    <PagePlaceholder
      eyebrow="A · Detection / C · Agent"
      title="센서 Trace·Agent 분석"
      description={`LOT 이력 ${lotHistId}의 센서 Trace와 Agent 분석 근거를 표시합니다.`}
    />
  )
}

export default TracePage
