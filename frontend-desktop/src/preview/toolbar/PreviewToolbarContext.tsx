import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react'
import type { ViewerCapabilities } from './capabilities'

interface Ctx {
  caps: ViewerCapabilities
  setCapabilities: (c: ViewerCapabilities) => void
}
const PreviewToolbarContext = createContext<Ctx | null>(null)

export function PreviewToolbarProvider({ children }: { children: ReactNode }) {
  const [caps, setCaps] = useState<ViewerCapabilities>({})
  const setCapabilities = useCallback((c: ViewerCapabilities) => setCaps(c), [])
  return (
    <PreviewToolbarContext.Provider value={{ caps, setCapabilities }}>
      {children}
    </PreviewToolbarContext.Provider>
  )
}

// Read current capabilities (used by the toolbar).
export function usePreviewToolbarState(): ViewerCapabilities {
  const ctx = useContext(PreviewToolbarContext)
  if (!ctx) throw new Error('usePreviewToolbarState must be used within PreviewToolbarProvider')
  return ctx.caps
}

// Register capabilities (used by a viewer). Re-registers when `deps` change,
// and clears on unmount so a switched viewer never inherits stale controls.
export function usePreviewToolbar(caps: ViewerCapabilities, deps: unknown[]): void {
  const ctx = useContext(PreviewToolbarContext)
  if (!ctx) throw new Error('usePreviewToolbar must be used within PreviewToolbarProvider')
  const { setCapabilities } = ctx
  useEffect(() => {
    setCapabilities(caps)
    return () => setCapabilities({})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
