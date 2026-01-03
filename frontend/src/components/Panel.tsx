import type { ReactNode } from 'react'

type Props = {
  title: string
  subtitle?: string
  right?: ReactNode
  className?: string
  bodyClassName?: string
  children: ReactNode
}

export function Panel({ title, subtitle, right, className, bodyClassName, children }: Props) {
  return (
    <section className={['panel', className].filter(Boolean).join(' ')}>
      <header className="panel__header">
        <div>
          <div className="panel__title">{title}</div>
          {subtitle ? <div className="panel__subtitle">{subtitle}</div> : null}
        </div>
        {right ? <div className="panel__right">{right}</div> : null}
      </header>
      <div className={['panel__body', bodyClassName].filter(Boolean).join(' ')}>{children}</div>
    </section>
  )
}


