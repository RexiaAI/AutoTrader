type Tone = 'good' | 'warn' | 'bad' | 'neutral'

type Props = {
  tone?: Tone
  label: string
  title?: string
}

export function Pill({ tone = 'neutral', label, title }: Props) {
  return (
    <span className={`pill pill--${tone}`} title={title}>
      {label}
    </span>
  )
}




