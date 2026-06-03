import { useEffect, useState } from 'react'
import { ConflictDialog } from './ConflictDialog'
import { KeysDrawer } from './KeysDrawer'
import { ModePill } from './ModePill'
import { useCanvasStore } from './store'
import { useCanvasUiStore } from './uiStore'
import { useRuntime } from './useRuntime'
import { useWalletStore } from '../setting/walletStore'

export function CanvasTopBar() {
  const templateName = useCanvasStore((s) => s.templateName)
  const setTemplateName = useCanvasStore((s) => s.setTemplateName)
  const resetToSeed = useCanvasStore((s) => s.resetToSeed)
  const consumeStartupFlash = useCanvasStore((s) => s.consumeStartupFlash)
  const bootstrapFromBackend = useCanvasStore((s) => s.bootstrapFromBackend)
  const saveStatus = useCanvasStore((s) => s.saveStatus)
  const [status, setStatus] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const keysOpen = useCanvasUiStore((s) => s.keysOpen)
  const setKeysOpen = useCanvasUiStore((s) => s.setKeysOpen)
  const rt = useRuntime()
  const execMode = useWalletStore((s) => s.execMode)

  const flash = (msg: string) => {
    setStatus(msg)
    window.setTimeout(() => setStatus(''), 1800)
  }

  const onRunPause = async () => {
    if (busy) return
    if (rt.overall === 'running') {
      setBusy(true)
      try {
        await rt.stop()
        flash('Paused')
      } catch {
        flash('Pause failed')
      } finally {
        setBusy(false)
      }
      return
    }
    if (execMode === 'live') {
      const ok = window.confirm(
        'Start strategy in LIVE mode?\nThis will place real orders with real funds.',
      )
      if (!ok) return
    }
    setBusy(true)
    try {
      await rt.start()
      flash('Strategy started')
    } catch {
      flash('Start failed')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    // canvas-sync: try to pull the backend canvas before reading the startup
    // flash, so a "Loaded canvas from backend" message can be set by the
    // bootstrap path itself. No-op when localStorage already had a draft.
    void bootstrapFromBackend().then(() => {
      const msg = consumeStartupFlash()
      if (msg) flash(msg)
    })
  }, [bootstrapFromBackend, consumeStartupFlash])

  return (
    <>
      <div className="h-11 shrink-0 border-b border-neutral-800 bg-neutral-950 flex items-center gap-3 px-4">
        <input
          type="text"
          value={templateName}
          onChange={(e) => setTemplateName(e.target.value)}
          placeholder="Strategy name"
          className="bg-transparent text-sm text-neutral-100 outline-none border-b border-transparent hover:border-neutral-700 focus:border-indigo-400 px-1 min-w-[200px]"
        />
        <span className="text-[11px] text-neutral-500 min-w-[180px]">{status}</span>
        <div className="ml-auto flex items-center gap-3">
          {/* Passive autosave indicator — the canvas persists on every change
              (v9 / SK3), so there is no manual Save button to forget. */}
          <span className="text-[11px] text-neutral-500">
            {saveStatus === 'saving'
              ? 'Saving…'
              : saveStatus === 'offline'
                ? '⚠ Offline — local draft only'
                : 'All changes saved'}
          </span>
          <ModePill />
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setKeysOpen(true)}
              title="Manage stored API keys"
              className="px-3 py-1 text-sm rounded border border-neutral-700 hover:border-neutral-600 hover:bg-neutral-900 text-neutral-100"
            >
              Keys
            </button>
            <button
              type="button"
              onClick={() => {
                resetToSeed()
                flash('Reset to seed template')
              }}
              title="Replace canvas with the seed template"
              className="px-3 py-1 text-sm rounded border border-neutral-700 hover:border-neutral-600 hover:bg-neutral-900 text-neutral-400"
            >
              Reset
            </button>
            {(() => {
              const running = rt.overall === 'running'
              if (!running && !rt.ready) {
                return (
                  <button
                    type="button"
                    disabled
                    title="Not ready — resolve the blockers below"
                    className="px-3 py-1 text-sm rounded font-medium border border-neutral-800 bg-neutral-900 text-neutral-600 cursor-not-allowed"
                  >
                    Run
                  </button>
                )
              }
              if (running) {
                return (
                  <button
                    type="button"
                    onClick={onRunPause}
                    disabled={busy}
                    title="Stop both sources"
                    className="px-3 py-1 text-sm rounded font-medium border border-neutral-700 bg-neutral-800 hover:bg-neutral-700 text-neutral-100 disabled:opacity-50"
                  >
                    {busy ? '…' : '❚❚ Pause'}
                  </button>
                )
              }
              const live = execMode === 'live'
              return (
                <button
                  type="button"
                  onClick={onRunPause}
                  disabled={busy}
                  title={live ? 'Start — LIVE (real funds)' : 'Start (paper)'}
                  className={`px-3 py-1 text-sm rounded font-medium text-white disabled:opacity-50 ${
                    live ? 'bg-red-600 hover:bg-red-500' : 'bg-emerald-600 hover:bg-emerald-500'
                  }`}
                >
                  {busy ? '…' : rt.overall === 'partial' ? '▶ Resume' : '▶ Run'}
                </button>
              )
            })()}
          </div>
        </div>
      </div>

      <KeysDrawer open={keysOpen} onClose={() => setKeysOpen(false)} />
      <ConflictDialog />
    </>
  )
}
