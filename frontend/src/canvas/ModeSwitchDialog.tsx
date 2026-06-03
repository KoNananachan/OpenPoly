/**
 * Modal that confirms paper/live switch. Surfaces guard failures from the
 * backend (open positions, missing wallet, invalid mnemonic) as inline
 * blockers so the operator does not need to read the network panel.
 *
 * When the blocker is `open_positions`, a "Close all" button bulk-routes
 * each open position through executor.execute_sell — same fill path as a
 * single manual close — so the operator can clear the blocker without
 * leaving the dialog.
 */
import { useEffect, useState } from 'react'

import {
  useWalletStore,
  type CloseAllResult,
  type ExecMode,
  type SwitchModeResult,
} from '../setting/walletStore'

type Props = {
  target: ExecMode
  onClose: () => void
}

function blockerMessage(result: SwitchModeResult): string | null {
  if (result.ok) return null
  switch (result.error) {
    case 'open_positions':
      return `${result.count ?? '?'} open positions must be closed first.`
    case 'wallet_not_configured':
      return 'Wallet is not configured. Set it in Keys → Wallet first.'
    case 'wallet_secret_missing':
      return result.message ?? 'private_key_ref does not resolve. Check your env / secrets.'
    case 'bad_private_key':
      return result.message ?? 'Private key is not a valid 0x-hex 64-char value.'
    case 'pusd_insufficient':
      return result.message ?? 'DepositWallet pUSD balance < 1. Fund it on polymarket.com first.'
    case 'standard_v2_not_approved':
      return result.message ?? 'Approve pUSD to Standard V2 Exchange at polymarket.com/settings.'
    case 'negrisk_v2_not_approved':
      return result.message ?? 'Approve pUSD to NegRisk V2 Exchange at polymarket.com/settings.'
    case 'rpc_unreachable':
      return result.message ?? 'CLOB unreachable. Network blocked? Wait + retry.'
    case 'live_executor_build_failed':
      return result.message ?? 'Live executor failed to construct. Check wallet config.'
    default:
      return result.message ?? `Switch refused: ${result.error}`
  }
}

function closeAllSummary(r: CloseAllResult): string {
  if (r.attempted === 0) return 'No open positions to close.'
  if (r.filled === r.attempted) return `Closed ${r.filled} positions.`
  const failed = r.skipped + r.errored
  const reasons = Array.from(
    new Set(
      r.details
        .filter((d) => !d.ok)
        .map((d) => d.skip_reason ?? (d.error ? 'executor_error' : 'unknown'))
    ),
  ).join(', ')
  return `Closed ${r.filled}/${r.attempted}. ${failed} failed (${reasons}). Residuals stay open — retry or close manually.`
}

export function ModeSwitchDialog({ target, onClose }: Props) {
  const switchMode = useWalletStore((s) => s.switchMode)
  const closeAllOpenPositions = useWalletStore((s) => s.closeAllOpenPositions)
  const wallet = useWalletStore((s) => s.wallet)
  const openCount = useWalletStore((s) => s.openPositionsCount)

  const [submitting, setSubmitting] = useState(false)
  const [closingAll, setClosingAll] = useState(false)
  const [blocker, setBlocker] = useState<string | null>(null)
  const [closeAllStatus, setCloseAllStatus] = useState<string | null>(null)

  // Pre-flight: surface the obvious blockers before the user clicks confirm.
  useEffect(() => {
    // Derived blocker state — setState in effect is intentional here.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (openCount > 0) setBlocker(`${openCount} open positions must be closed first.`)
    else if (target === 'live' && (!wallet || wallet.error))
      setBlocker(
        wallet?.error
          ? `Wallet ref problem: ${wallet.error}`
          : 'Wallet is not configured. Set it in Keys → Wallet first.',
      )
    else setBlocker(null)
  }, [openCount, target, wallet])

  async function onConfirm() {
    setSubmitting(true)
    const result = await switchMode(target)
    setSubmitting(false)
    if (result.ok) {
      onClose()
    } else {
      setBlocker(blockerMessage(result) ?? 'Switch refused.')
    }
  }

  async function onCloseAll() {
    if (closingAll) return
    if (
      !window.confirm(
        `Close all ${openCount} open positions at level-1 bid?\n\n` +
          'Each position routes through the same execute_sell as a single ' +
          'manual close. Failures (e.g. no bid liquidity) will be reported ' +
          'and those positions stay open.',
      )
    ) {
      return
    }
    setClosingAll(true)
    setCloseAllStatus(null)
    try {
      const result = await closeAllOpenPositions()
      setCloseAllStatus(closeAllSummary(result))
    } catch (e) {
      setCloseAllStatus(
        `Close-all request failed: ${e instanceof Error ? e.message : String(e)}`,
      )
    } finally {
      setClosingAll(false)
    }
  }

  const isLive = target === 'live'
  const headerColor = isLive ? 'text-red-300' : 'text-sky-300'
  const blockerIsOpenPositions = openCount > 0

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-[480px] max-w-[calc(100vw-2rem)] rounded-lg border border-neutral-800 bg-neutral-950 shadow-2xl p-5 flex flex-col gap-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className={`text-sm font-medium ${headerColor}`}>
          Switch to {target.toUpperCase()}
        </h3>

        <p className="text-xs text-neutral-400 leading-relaxed">
          {isLive ? (
            <>
              Live mode signs and submits real IOC orders on Polygon mainnet with
              the configured wallet (Polymarket CLOB).{' '}
              <span className="text-amber-300">
                Settlement detection (slice E) and kill-switch enforcement (A4)
                are not yet implemented — open positions will not auto-close on
                market resolution, and there is no hard daily-loss / drawdown
                brake beyond the entry-side heat_cap. Monitor positions
                manually until those land.
              </span>
            </>
          ) : (
            'Paper mode disables real-money trading. All fills use a virtual ledger.'
          )}
        </p>

        <div className="text-xs flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-neutral-500">Open positions:</span>
            <span className="text-neutral-200">{openCount}</span>
            {openCount > 0 && (
              <a
                href="/activity"
                target="_blank"
                rel="noreferrer"
                className="text-sky-400 hover:text-sky-300 underline"
              >
                view →
              </a>
            )}
          </div>
          {isLive && (
            <>
              <div>
                <span className="text-neutral-500">Signer EOA: </span>
                <span className="text-neutral-200">
                  {wallet?.signer_address ?? '(unconfigured)'}
                </span>
              </div>
              <div>
                <span className="text-neutral-500">Funder    : </span>
                <span className="text-neutral-200">
                  {wallet?.funder_address ?? '(unconfigured)'}
                </span>
              </div>
            </>
          )}
        </div>

        {blocker && (
          <div className="rounded border border-red-900 bg-red-950 text-red-200 text-xs p-2 break-words flex flex-col gap-2">
            <div>{blocker}</div>
            {blockerIsOpenPositions && (
              <div className="text-amber-200 text-[11px] leading-snug">
                Heads up: with{' '}
                <code className="text-amber-100">
                  same_market_lifetime_lockout
                </code>{' '}
                on, closing these will permanently lock their (market, side)
                from future re-entry.
              </div>
            )}
            {blockerIsOpenPositions && (
              <button
                type="button"
                disabled={closingAll}
                onClick={() => void onCloseAll()}
                className="self-start px-2 py-1 text-xs rounded bg-red-800 hover:bg-red-700 disabled:bg-neutral-800 disabled:text-neutral-500 text-red-50"
              >
                {closingAll
                  ? `Closing ${openCount}…`
                  : `Close all ${openCount} positions`}
              </button>
            )}
          </div>
        )}

        {closeAllStatus && (
          <div className="rounded border border-neutral-700 bg-neutral-900 text-neutral-200 text-xs p-2 break-words">
            {closeAllStatus}
          </div>
        )}

        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1 text-sm rounded border border-neutral-700 hover:border-neutral-600 hover:bg-neutral-900 text-neutral-200"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={blocker !== null || submitting || closingAll}
            onClick={() => void onConfirm()}
            className={`px-3 py-1 text-sm rounded ${
              isLive
                ? 'bg-red-700 hover:bg-red-600 disabled:bg-neutral-800 disabled:text-neutral-600'
                : 'bg-sky-700 hover:bg-sky-600 disabled:bg-neutral-800 disabled:text-neutral-600'
            } text-neutral-50`}
          >
            {submitting ? 'Switching…' : `Switch to ${target.toUpperCase()}`}
          </button>
        </div>
      </div>
    </div>
  )
}
