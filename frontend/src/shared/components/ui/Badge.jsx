// fdc.css .badge — radius 999px, 높이 20px, mono 10.5px/600
const VARIANTS = {
  'bg-red': 'bg-red text-white',
  'bg-blue': 'bg-blue text-white',
  'bg-navy': 'bg-navy text-white',
  'bg-gray': 'bg-g2 text-white',
  'bg-green': 'bg-green text-white',
  'bg-amber': 'bg-amber text-white',
  't-red': 'bg-tint-red border border-tint-red-line text-red',
  't-amber': 'bg-tint-amber border border-tint-amber-line text-tint-amber-text',
  't-green': 'bg-tint-green border border-tint-green-line text-green',
  't-gray': 'bg-tint-gray border border-tint-gray-line text-g1',
  't-blue': 'bg-tint-blue border border-tint-blue-line text-blue',
  't-navy': 'bg-tint-navy border border-tint-navy-line text-navy',
}

function Badge({ variant = 't-gray', className = '', children }) {
  return (
    <span
      className={`inline-flex h-5 items-center justify-center whitespace-nowrap rounded-full px-2.5 font-mono text-[10.5px] font-semibold tracking-[.02em] ${VARIANTS[variant] ?? VARIANTS['t-gray']} ${className}`}
    >
      {children}
    </span>
  )
}

export default Badge
