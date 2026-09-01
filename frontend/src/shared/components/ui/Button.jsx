// fdc.css .btn / .btn-primary / .btn-outline / .btn-outline-red / .btn-sm
const VARIANTS = {
  primary: 'bg-blue text-white hover:bg-blue-hover border-transparent',
  outline: 'bg-white border-line text-navy',
  'outline-red': 'bg-white border-red text-red',
}

function Button({ variant = 'primary', sm = false, className = '', children, ...rest }) {
  return (
    <button
      type="button"
      className={`inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-lg border font-sans font-bold ${
        sm ? 'h-7 px-3.5 text-xs' : 'h-[34px] px-[18px] text-[13px]'
      } ${VARIANTS[variant] ?? VARIANTS.primary} disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}

export default Button
