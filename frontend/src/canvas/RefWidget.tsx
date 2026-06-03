/**
 * RJSF widget for `*_ref` secret-reference fields (v9 / SK1).
 *
 * Replaces the old "paste raw secret + Save secrets" flow. The section node
 * config now only ever holds a ref string — never a raw secret:
 *
 * - dropdown mode: pick an existing stored key → value becomes `local:<name>`;
 *   "+ New key…" opens AddKeyModal; a dangling `local:` ref (the key was
 *   deleted) is flagged, never silently dropped.
 * - manual mode: free-text for `env:` / `vault:` / `keychain:` refs.
 */
import type { WidgetProps } from '@rjsf/utils'
import { useMemo, useState } from 'react'

import { AddKeyModal } from '../setting/StoredKeysPanel'
import { useSecretsStore } from '../setting/secretsStore'

// Sentinel option values — distinct from any real `local:`/`env:` ref.
const OPT_NEW = '__new__'
const OPT_MANUAL = '__manual__'

const LOCAL_PREFIX = 'local:'

export function RefWidget(props: WidgetProps) {
  const { value, onChange, disabled, readonly } = props
  const strValue = typeof value === 'string' ? value : ''
  const locked = Boolean(disabled || readonly)

  const keys = useSecretsStore((s) => s.keys)
  const status = useSecretsStore((s) => s.status)

  // Mode is seeded once from the incoming value: `local:` / empty → dropdown,
  // any other scheme (env:, vault:, …) → manual text entry.
  const [manual, setManual] = useState(
    strValue !== '' && !strValue.startsWith(LOCAL_PREFIX),
  )
  const [showAdd, setShowAdd] = useState(false)

  const knownNames = useMemo(
    () => new Set(keys.map((k) => k.name)),
    [keys],
  )
  const currentLocalName = strValue.startsWith(LOCAL_PREFIX)
    ? strValue.slice(LOCAL_PREFIX.length)
    : null
  // `missing` covers "not loaded yet" too; `dangling` is the confirmed case.
  const missing = currentLocalName !== null && !knownNames.has(currentLocalName)
  const dangling = missing && status === 'ready'

  if (manual) {
    return (
      <div className="flex flex-col gap-1">
        <input
          type="text"
          value={strValue}
          disabled={locked}
          placeholder="env:VAR_NAME"
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className="self-start text-[11px] text-indigo-300 hover:text-indigo-200"
          onClick={() => setManual(false)}
        >
          ▾ pick a stored key instead
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1">
      <select
        value={strValue}
        disabled={locked}
        onChange={(e) => {
          const v = e.target.value
          if (v === OPT_NEW) {
            setShowAdd(true)
            return
          }
          if (v === OPT_MANUAL) {
            setManual(true)
            return
          }
          onChange(v)
        }}
      >
        <option value="">— select a stored key —</option>
        {missing && currentLocalName !== null && (
          <option value={strValue}>
            {dangling ? `${currentLocalName} (missing)` : currentLocalName}
          </option>
        )}
        {keys.map((k) => (
          <option key={k.name} value={`${LOCAL_PREFIX}${k.name}`}>
            {k.name}
          </option>
        ))}
        <option value={OPT_NEW}>+ New key…</option>
        <option value={OPT_MANUAL}>Manual entry (env: / vault: …)</option>
      </select>

      {dangling && currentLocalName !== null && (
        <span className="text-[11px] text-red-300">
          Referenced key <code>{currentLocalName}</code> not found in the local
          store — pick another or re-create it.
        </span>
      )}
      {status === 'error' && (
        <span className="text-[11px] text-amber-300">
          Backend unreachable — stored keys unavailable.
        </span>
      )}

      {showAdd && (
        <AddKeyModal
          onClose={() => setShowAdd(false)}
          onAdded={(name) => {
            onChange(`${LOCAL_PREFIX}${name}`)
            setShowAdd(false)
          }}
        />
      )}
    </div>
  )
}
