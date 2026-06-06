import { describe, it, expect, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { LanguageProvider } from '@/i18n'
import { PreviewToolbarProvider, usePreviewToolbar } from './PreviewToolbarContext'
import { PreviewToolbar } from './PreviewToolbar'

const goto = vi.fn()

function Registrar() {
  usePreviewToolbar({
    toc: { items: [{ id: 'a', label: 'Intro', level: 0 }, { id: 'b', label: 'Deep', level: 1 }], goto },
  }, [])
  return null
}

function harness() {
  return render(
    <LanguageProvider>
      <PreviewToolbarProvider>
        <PreviewToolbar />
        <Registrar />
      </PreviewToolbarProvider>
    </LanguageProvider>,
  )
}

describe('PreviewToolbar TOC control', () => {
  it('shows a Contents button, opens a panel, and navigates on click', () => {
    harness()
    const btn = screen.getByTitle('Contents')
    expect(btn).toBeInTheDocument()
    act(() => { btn.click() })
    const item = screen.getByText('Deep')
    expect(item).toBeInTheDocument()
    act(() => { item.click() })
    expect(goto).toHaveBeenCalledWith('b')
  })
})
