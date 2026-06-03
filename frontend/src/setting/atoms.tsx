import type { ReactNode } from 'react'

export const inputCls =
  'w-full rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-sm text-neutral-100 outline-none focus:border-indigo-400'

export const labelCls = 'flex flex-col gap-1 text-xs text-neutral-400'

export function Card({
  title,
  count,
  children,
  action,
}: {
  title: string
  count?: number
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <section className="rounded border border-neutral-800 bg-neutral-900/40">
      <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-neutral-800">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-medium text-neutral-100">{title}</h2>
          {count !== undefined && (
            <span className="text-xs text-neutral-500">({count})</span>
          )}
        </div>
        {action}
      </header>
      <div className="p-4">{children}</div>
    </section>
  )
}

export function PrimaryButton({
  onClick,
  children,
  disabled,
}: {
  onClick: () => void
  children: ReactNode
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="px-3 py-1 text-xs rounded border border-indigo-400/40 bg-indigo-500/10 text-indigo-200 hover:bg-indigo-500/20 disabled:opacity-40 disabled:cursor-not-allowed"
    >
      {children}
    </button>
  )
}

export function GhostButton({
  onClick,
  children,
  variant = 'default',
  disabled,
  title,
}: {
  onClick: () => void
  children: ReactNode
  variant?: 'default' | 'danger'
  disabled?: boolean
  title?: string
}) {
  const cls =
    variant === 'danger'
      ? 'text-red-400 hover:bg-red-500/10 border-red-500/30'
      : 'text-neutral-300 hover:bg-neutral-800 border-neutral-700'
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`px-2 py-1 text-xs rounded border ${cls} disabled:opacity-40 disabled:cursor-not-allowed`}
    >
      {children}
    </button>
  )
}
