import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

function MarkdownContent({ content, className = '' }) {
  if (!content) return null

  return (
    <div className={`markdown-content ${className}`.trim()}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
}

export default MarkdownContent
