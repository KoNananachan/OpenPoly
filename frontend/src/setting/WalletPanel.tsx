/**
 * Wallet card — sits above StoredKeysPanel in the Keys drawer.
 *
 * Polymarket V2 wallet model: operator points the system at a
 * private_key_ref (any stored secret or `env:VAR` reference) for the EOA
 * signer, plus the on-chain DepositWallet address that holds pUSD +
 * positions. Signer EOA is derived server-side and shown read-only.
 */
import { useEffect, useState } from 'react'

import { Card, GhostButton, PrimaryButton, inputCls, labelCls } from './atoms'
import { useWalletStore } from './walletStore'

export function WalletPanel() {
  const wallet = useWalletStore((s) => s.wallet)
  const status = useWalletStore((s) => s.status)
  const load = useWalletStore((s) => s.load)
  const saveWallet = useWalletStore((s) => s.saveWallet)

  const [privateKeyRef, setPrivateKeyRef] = useState('')
  const [funderAddress, setFunderAddress] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (status === 'idle') void load()
  }, [status, load])

  useEffect(() => {
    if (wallet) {
      // Hydrate local form state from fetched wallet config; intentional.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPrivateKeyRef(wallet.private_key_ref ?? '')
      setFunderAddress(wallet.funder_address ?? '')
    }
  }, [wallet])

  async function onSave() {
    setError(null)
    setBusy(true)
    try {
      await saveWallet(privateKeyRef.trim(), funderAddress.trim())
    } catch (e) {
      const detail = (e as { detail?: { error?: string; message?: string } }).detail
      setError(detail?.message ?? detail?.error ?? (e instanceof Error ? e.message : String(e)))
    } finally {
      setBusy(false)
    }
  }

  const canSubmit =
    privateKeyRef.trim() !== '' && funderAddress.trim() !== '' && !busy

  return (
    <Card
      title="Wallet (live trading)"
      action={<GhostButton onClick={() => void load()}>Refresh</GhostButton>}
    >
      <div className="flex flex-col gap-3">
        <label className={labelCls}>
          <span>Private key ref (EOA signer)</span>
          <input
            type="text"
            className={inputCls}
            value={privateKeyRef}
            placeholder="env:OPENPOLY_POLYMARKET_PK"
            onChange={(e) => setPrivateKeyRef(e.target.value)}
          />
          <span className="text-[11px] text-neutral-500">
            Reference to the 0x-hex EOA private key that signs orders.
            Recommended: <code>env:OPENPOLY_POLYMARKET_PK</code> in <code>.env</code>.
          </span>
        </label>

        <label className={labelCls}>
          <span>Funder address (DepositWallet)</span>
          <input
            type="text"
            className={inputCls}
            value={funderAddress}
            placeholder="0x1234567890123456789012345678901234567890"
            onChange={(e) => setFunderAddress(e.target.value)}
          />
          <span className="text-[11px] text-neutral-500">
            On-chain Polymarket DepositWallet contract address (the wallet
            holding pUSD + CTF positions). Find at{' '}
            <a
              className="text-sky-400 hover:text-sky-300 underline"
              href="https://polymarket.com/settings"
              target="_blank"
              rel="noreferrer"
            >
              polymarket.com/settings
            </a>
            .
          </span>
        </label>

        <div className="text-xs flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-neutral-500">Signer EOA:</span>
            {wallet?.signer_address ? (
              <code className="text-neutral-200 truncate" title={wallet.signer_address}>
                {wallet.signer_address}
              </code>
            ) : (
              <span className="text-neutral-500">
                — {wallet?.error ? `(${wallet.error})` : '(unconfigured)'}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-neutral-500">Funder    :</span>
            {wallet?.funder_address ? (
              <code className="text-neutral-200 truncate" title={wallet.funder_address}>
                {wallet.funder_address}
              </code>
            ) : (
              <span className="text-neutral-500">—</span>
            )}
          </div>
        </div>

        {error && (
          <div className="text-xs text-red-300 break-words">{error}</div>
        )}

        <div className="flex justify-end">
          <PrimaryButton onClick={onSave} disabled={!canSubmit}>
            {busy ? 'Saving…' : 'Save'}
          </PrimaryButton>
        </div>
      </div>
    </Card>
  )
}
