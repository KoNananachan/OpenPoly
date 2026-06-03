/**
 * Canvas-level "Keys" drawer (v9 / SK2).
 *
 * Replaces the removed global Setting page. Hosts the stored-key CRUD panel
 * so secrets can be managed without leaving the canvas. Opened from the
 * CanvasTopBar "Keys" button; backdrop click closes.
 */
import { StoredKeysPanel } from '../setting/StoredKeysPanel'
import { WalletPanel } from '../setting/WalletPanel'

export function KeysDrawer({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  if (!open) return null
  return (
    // Backdrop starts BELOW the top bar (top-11 = 44px = h-11) so the top-bar
    // controls (mode pill, Keys, Reset, Run) stay un-dimmed and click-through
    // without each needing its own z-index. Clicking the dimmed area still
    // closes the drawer; clicking the top bar interacts with it normally.
    <div
      className="fixed left-0 right-0 bottom-0 top-11 z-40 bg-black/50"
      onClick={onClose}
    >
      <div
        className="absolute right-0 top-0 bottom-0 w-[460px] max-w-[calc(100vw-2rem)] border-l border-neutral-800 bg-neutral-950 shadow-2xl overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-800">
          <h2 className="text-sm font-medium text-neutral-100">Keys</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-neutral-400 hover:text-neutral-100 text-lg leading-none"
          >
            ×
          </button>
        </div>
        <div className="p-5 flex flex-col gap-5">
          <WalletPanel />
          <StoredKeysPanel />
        </div>
      </div>
    </div>
  )
}
