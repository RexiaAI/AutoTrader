import type { ReactNode } from 'react'

type Props = {
  label: string
  value: ReactNode
  sub?: ReactNode
}

export function Kpi({ label, value, sub }: Props) {
  return (
    <div className="kpi">
      <div className="kpi__label">{label}</div>
      <div className="kpi__value">{value}</div>
      {sub ? <div className="kpi__sub">{sub}</div> : <div className="kpi__sub">&nbsp;</div>}
    </div>
  )
}




