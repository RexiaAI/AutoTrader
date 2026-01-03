import type { ReactNode } from 'react'

export type Column<T> = {
  header: string
  render: (row: T) => ReactNode
  align?: 'left' | 'right' | 'centre'
  width?: string
}

type Props<T> = {
  columns: Array<Column<T>>
  rows: T[]
  rowKey: (row: T, idx: number) => string | number
  emptyText?: string
  height?: number | string
  maxHeight?: number | string
  onRowClick?: (row: T) => void
}

export function Table<T>({
  columns,
  rows,
  rowKey,
  emptyText = 'No data yet.',
  height,
  maxHeight,
  onRowClick,
}: Props<T>) {
  return (
    <div className="tableWrap" style={{ ...(height ? { height } : null), ...(maxHeight ? { maxHeight } : null) }}>
      <table className="table">
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th
                key={i}
                style={{ width: c.width }}
                className={c.align === 'right' ? 'ta-r' : c.align === 'centre' ? 'ta-c' : undefined}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="table__empty">
                {emptyText}
              </td>
            </tr>
          ) : (
            rows.map((r, idx) => (
              <tr
                key={rowKey(r, idx)}
                className={onRowClick ? 'table__rowClickable' : undefined}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
              >
                {columns.map((c, i) => (
                  <td
                    key={i}
                    className={c.align === 'right' ? 'ta-r' : c.align === 'centre' ? 'ta-c' : undefined}
                  >
                    {c.render(r)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}


