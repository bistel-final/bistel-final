import { Navigate, useLocation } from 'react-router-dom'

function KnowledgeRedirect() {
  const { search } = useLocation()
  return <Navigate to={{ pathname: '/ontology', search }} replace />
}

export default KnowledgeRedirect
