function PagePlaceholder({ eyebrow, title, description, children }) {
  return (
    <section className="page-placeholder">
      <p className="page-eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      <p>{description}</p>
      {children}
    </section>
  )
}

export default PagePlaceholder
