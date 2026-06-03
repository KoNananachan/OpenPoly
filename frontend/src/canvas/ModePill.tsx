/**
 * Exec-mode toggle — a segmented Paper | Live control. The overall run state
 * lives in the status bar now, so this is a calm switch rather than an alarm
 * badge. Clicking the inactive segment still routes through ModeSwitchDialog,
 * which runs the live preflight (balance / allowances) before any switch.
 */
import { useEffect, useState } from 'react'

import { useWalletStore } from '../setting/walletStore'
import { ModeSwitchDialog } from './ModeSwitchDialog'

type Mode = 'paper' | 'live'

export function ModePill() {
  const execMode = useWalletStore((s) => s.execMode)
  const status = useWalletStore((s) => s.status)
  const load = useWalletStore((s) => s.load)
  const [dialogTarget, setDialogTarget] = useState<Mode | null>(null)

  useEffect(() => {
    if (status === 'idle') void load()
  }, [status, load])

  const segClass = (mode: Mode) => {
    const base = 'px-2.5 py-0.5 text-xs font-medium rounded-[5px] transition-colors'
    // Single neutral highlight for whichever mode is active — the overall run
    // state and live/paper safety live in the status bar and Run button.
    return execMode === mode
      ? `${base} bg-neutral-700 text-neutral-100`
      : `${base} text-neutral-500 hover:text-neutral-300`
  }

  const onPick = (mode: Mode) => {
    if (execMode !== mode) setDialogTarget(mode)
  }

  return (
    <>
      <div
        role="group"
        aria-label="Execution mode"
        title={execMode === 'live' ? 'LIVE — real funds' : 'Paper — no real funds'}
        className="inline-flex items-center rounded-md border border-neutral-700 bg-neutral-900 p-0.5"
      >
        <button type="button" onClick={() => onPick('paper')} className={segClass('paper')}>
          Paper
        </button>
        <button type="button" onClick={() => onPick('live')} className={segClass('live')}>
          Live
        </button>
      </div>
      {dialogTarget && (
        <ModeSwitchDialog target={dialogTarget} onClose={() => setDialogTarget(null)} />
      )}
    </>
  )
}
