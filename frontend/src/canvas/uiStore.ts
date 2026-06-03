/**
 * Small shared UI store for cross-component canvas chrome — currently just the
 * Keys drawer open state, so both the top bar and the status bar's "Open Keys"
 * blocker action can toggle it.
 */
import { create } from 'zustand'

type UiState = {
  keysOpen: boolean
  setKeysOpen: (open: boolean) => void
}

export const useCanvasUiStore = create<UiState>((set) => ({
  keysOpen: false,
  setKeysOpen: (keysOpen) => set({ keysOpen }),
}))
