/**
 * Stored keys panel. Lists names registered in the backend local secret store
 * (values are write-only), with add via a modal and delete via window.confirm.
 *
 * `AddKeyModal` is exported for reuse by RefWidget's "+ New key" action.
 */
import { useState } from 'react'

import { Card, GhostButton, PrimaryButton, inputCls, labelCls } from './atoms'
import { useSecretsStore } from './secretsStore'

export function StoredKeysPanel() {
  const keys = useSecretsStore((s) => s.keys)
  const status = useSecretsStore((s) => s.status)
  const error = useSecretsStore((s) => s.error)
  const remove = useSecretsStore((s) => s.remove)
  const refresh = useSecretsStore((s) => s.refresh)

  const [showModal, setShowModal] = useState(false)
  const [rowError, setRowError] = useState<string | null>(null)

  async function handleDelete(name: string) {
    if (!window.confirm(`Delete stored key "${name}"? This cannot be undone.`)) {
      return
    }
    setRowError(null)
    try {
      await remove(name)
    } catch (e) {
      setRowError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <>
      <Card
        title="Stored keys (local)"
        count={keys.length}
        action={
          <div className="flex gap-2">
            <GhostButton onClick={() => void refresh()}>Refresh</GhostButton>
            <PrimaryButton onClick={() => setShowModal(true)}>
              + Add key
            </PrimaryButton>
          </div>
        }
      >
        {status === 'loading' && keys.length === 0 && (
          <div className="text-xs text-neutral-500 py-2">Loading…</div>
        )}
        {status === 'error' && (
          <div className="text-xs text-red-300 mb-2 break-words">
            Backend unreachable: {error}
          </div>
        )}
        {status === 'ready' && keys.length === 0 && (
          <div className="text-xs text-neutral-500 py-2">
            No keys stored. Add one to reference via{' '}
            <code className="text-neutral-300">local:&lt;name&gt;</code> in
            section configs.
          </div>
        )}
        {keys.length > 0 && (
          <ul className="flex flex-col gap-1">
            {keys.map((k) => (
              <KeyRow
                key={k.name}
                name={k.name}
                createdAt={k.created_at}
                onDelete={() => void handleDelete(k.name)}
              />
            ))}
          </ul>
        )}
        {rowError && (
          <div className="text-xs text-red-300 mt-2 break-words">{rowError}</div>
        )}
      </Card>

      {showModal && (
        <AddKeyModal
          onClose={() => setShowModal(false)}
          onAdded={() => setShowModal(false)}
        />
      )}
    </>
  )
}

function KeyRow({
  name,
  createdAt,
  onDelete,
}: {
  name: string
  createdAt: number
  onDelete: () => void
}) {
  const depth = Math.max(0, name.split('/').length - 1)
  const date = new Date(createdAt * 1000).toISOString().split('T')[0]
  return (
    <li
      className="flex items-center gap-3 rounded border border-neutral-800 bg-neutral-950 px-3 py-1.5"
      style={{ paddingLeft: `${0.75 + depth * 0.85}rem` }}
    >
      <div className="flex-1 min-w-0">
        <code
          className="text-xs text-neutral-200 truncate block"
          title={`local:${name}`}
        >
          {name}
        </code>
        <div className="text-[10px] text-neutral-500">created {date}</div>
      </div>
      <GhostButton onClick={onDelete} variant="danger">
        Delete
      </GhostButton>
    </li>
  )
}

/**
 * Modal to register a new key in the local secret store. `onAdded` receives
 * the created name so callers (RefWidget) can immediately reference it.
 */
export function AddKeyModal({
  onClose,
  onAdded,
}: {
  onClose: () => void
  onAdded: (name: string) => void
}) {
  const add = useSecretsStore((s) => s.add)

  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const trimmedName = name.trim()
  const canSubmit = trimmedName !== '' && value !== '' && !busy

  async function submit() {
    if (!canSubmit) return
    setError(null)
    setBusy(true)
    try {
      await add(trimmedName, value)
      onAdded(trimmedName)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-[460px] max-w-[calc(100vw-2rem)] rounded-lg border border-neutral-800 bg-neutral-950 shadow-2xl p-5 flex flex-col gap-3"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-medium text-neutral-100">Add stored key</h3>

        <label className={labelCls}>
          <span>Name</span>
          <input
            type="text"
            className={inputCls}
            value={name}
            placeholder="tradingnews-key"
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
          <span className="text-[11px] text-neutral-500">
            Allowed chars: letters, digits, <code>_</code>, <code>-</code>,{' '}
            <code>/</code>.
          </span>
        </label>

        <label className={labelCls}>
          <span>Value</span>
          <input
            type="password"
            className={inputCls}
            value={value}
            placeholder="paste secret here"
            onChange={(e) => setValue(e.target.value)}
          />
          <span className="text-[11px] text-neutral-500">
            Stored locally at <code>~/.openpoly/secrets.json</code> (chmod 600).
            Never readable via HTTP.
          </span>
        </label>

        {error && (
          <div className="text-xs text-red-300 break-words">{error}</div>
        )}

        <div className="flex gap-2 justify-end">
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton onClick={submit} disabled={!canSubmit}>
            {busy ? 'Saving…' : 'Save'}
          </PrimaryButton>
        </div>
      </div>
    </div>
  )
}
