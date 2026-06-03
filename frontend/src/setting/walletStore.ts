/**
 * Wallet + exec-mode runtime state, backed by /api/wallet/config and
 * /api/system/mode. The store is intentionally pull-based — the UI calls
 * load() when relevant panels mount; there is no SSE / WS push (slice C+).
 *
 * Wallet model is Polymarket V2 DepositWallet:
 *   - private_key_ref → resolves to the 0x-hex EOA signer key
 *   - funder_address  → the DepositWallet contract holding pUSD + positions
 */
import { create } from 'zustand'

export type ExecMode = 'paper' | 'live'

export type WalletConfig = {
  private_key_ref: string | null
  funder_address: string | null
  signer_address: string | null
  error: string | null
}

export type SwitchModeResult =
  | { ok: true; mode: ExecMode }
  | {
      ok: false
      error: string
      count?: number
      message?: string
    }

export type CloseAllResult = {
  attempted: number
  filled: number
  skipped: number
  errored: number
  details: {
    position_id: number
    market_id: string
    side: string
    ok: boolean
    price?: number
    qty?: number
    skip_reason?: string
    error?: string
  }[]
}

type WalletStore = {
  wallet: WalletConfig | null
  execMode: ExecMode
  openPositionsCount: number
  status: 'idle' | 'loading' | 'ready' | 'error'
  errorMessage: string | null

  load: () => Promise<void>
  saveWallet: (private_key_ref: string, funder_address: string) => Promise<WalletConfig>
  switchMode: (target: ExecMode) => Promise<SwitchModeResult>
  closeAllOpenPositions: () => Promise<CloseAllResult>
}

async function jsonOr<T>(r: Response): Promise<T> {
  const body = await r.json()
  if (!r.ok) {
    const err = body?.detail ?? body
    throw Object.assign(new Error(err?.message ?? r.statusText), { detail: err })
  }
  return body as T
}

export const useWalletStore = create<WalletStore>((set) => ({
  wallet: null,
  execMode: 'paper',
  openPositionsCount: 0,
  status: 'idle',
  errorMessage: null,

  async load() {
    set({ status: 'loading', errorMessage: null })
    try {
      const [walletResp, modeResp, positionsResp] = await Promise.all([
        fetch('/api/wallet/config'),
        fetch('/api/system/mode'),
        fetch('/api/positions'),
      ])
      const wallet = await jsonOr<WalletConfig>(walletResp)
      const mode = await jsonOr<{ mode: ExecMode }>(modeResp)
      const positions = await jsonOr<{ positions: { status: string }[] }>(positionsResp)
      const openCount = positions.positions?.filter((p) => p.status === 'open').length ?? 0
      set({
        wallet,
        execMode: mode.mode,
        openPositionsCount: openCount,
        status: 'ready',
      })
    } catch (e) {
      set({
        status: 'error',
        errorMessage: e instanceof Error ? e.message : String(e),
      })
    }
  },

  async saveWallet(private_key_ref, funder_address) {
    const r = await fetch('/api/wallet/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ private_key_ref, funder_address }),
    })
    const updated = await jsonOr<WalletConfig>(r)
    set({ wallet: updated })
    return updated
  },

  async switchMode(target) {
    const r = await fetch('/api/system/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: target }),
    })
    if (r.ok) {
      const body = (await r.json()) as { mode: ExecMode }
      set({ execMode: body.mode })
      return { ok: true, mode: body.mode }
    }
    const body = await r.json()
    const detail = body?.detail ?? {}
    return {
      ok: false,
      error: detail.error ?? 'unknown',
      count: detail.count,
      message: detail.message,
    }
  },

  async closeAllOpenPositions() {
    const r = await fetch('/api/positions/close-all', { method: 'POST' })
    const result = await jsonOr<CloseAllResult>(r)
    const remaining = Math.max(
      0,
      (useWalletStore.getState().openPositionsCount ?? 0) - result.filled,
    )
    set({ openPositionsCount: remaining })
    return result
  },
}))
