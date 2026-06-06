import { useState } from 'react'
import {
  ZoomIn, ZoomOut, Maximize, Download, Copy,
  Check, Search, ChevronUp, ChevronDown, ListTree,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { usePreviewToolbarState } from './PreviewToolbarContext'

export function PreviewToolbar() {
  const { t } = useI18n()
  const caps = usePreviewToolbarState()
  const [copied, setCopied] = useState(false)
  const [query, setQuery] = useState('')
  const [tocOpen, setTocOpen] = useState(false)

  const hasAny = caps.zoom || caps.pages || caps.search || caps.download || caps.copy || caps.toc
  if (!hasAny) return null

  function doCopy() {
    if (!caps.copy) return
    void navigator.clipboard.writeText(caps.copy()).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    })
  }

  return (
    <div className="flex items-center gap-1 px-3 py-1.5 flex-shrink-0"
      style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg2)' }}>
      {caps.zoom && (
        <>
          <Button variant="ghost" size="icon" title={t('preview.zoomOut')} onClick={() => caps.zoom!.out()}>
            <ZoomOut size={15} />
          </Button>
          <span className="text-xs tabular-nums w-10 text-center" style={{ color: 'var(--t2)' }}>
            {Math.round(caps.zoom.scale * 100)}%
          </span>
          <Button variant="ghost" size="icon" title={t('preview.zoomIn')} onClick={() => caps.zoom!.in()}>
            <ZoomIn size={15} />
          </Button>
          <Button variant="ghost" size="icon" title={t('preview.zoomFit')} onClick={() => caps.zoom!.fit()}>
            <Maximize size={15} />
          </Button>
        </>
      )}

      {caps.toc && caps.toc.items.length > 0 && (
        <div className="relative">
          <Button variant="ghost" size="icon" title={t('preview.toc')} onClick={() => setTocOpen((o) => !o)}>
            <ListTree size={15} />
          </Button>
          {tocOpen && (
            <div className="absolute left-0 top-full mt-1 z-20 max-h-80 overflow-auto rounded py-1"
              style={{ background: 'var(--bg1)', border: '1px solid var(--border)', minWidth: 220, boxShadow: '0 8px 24px rgba(15,31,61,.18)' }}>
              {caps.toc.items.map((item) => (
                <button
                  key={item.id}
                  onClick={() => { caps.toc!.goto(item.id); setTocOpen(false) }}
                  className="block w-full text-left text-xs py-1 truncate"
                  style={{ paddingLeft: 8 + (item.level ?? 0) * 12, paddingRight: 8, color: 'var(--t2)', background: 'transparent', border: 'none', cursor: 'pointer' }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {caps.pages && (
        <span className="text-xs px-2" style={{ color: 'var(--t2)' }}>
          {t('preview.page')} {caps.pages.current} / {caps.pages.count}
        </span>
      )}

      {caps.search && (
        <div className="flex items-center gap-1 ml-1">
          <Search size={14} style={{ color: 'var(--t3)' }} />
          <input
            value={query}
            onChange={(e) => { setQuery(e.target.value); caps.search!.run(e.target.value) }}
            placeholder={t('preview.searchPlaceholder')}
            className="text-xs px-2 py-1 rounded outline-none"
            style={{ background: 'var(--bg1)', border: '1px solid var(--border)', color: 'var(--t1)', width: 160 }}
          />
          {typeof caps.search.count === 'number' && (
            <span className="text-xs tabular-nums" style={{ color: 'var(--t3)' }}>
              {caps.search.count}
            </span>
          )}
          <Button variant="ghost" size="icon" title={t('preview.prevMatch')} onClick={() => caps.search!.prev()}>
            <ChevronUp size={15} />
          </Button>
          <Button variant="ghost" size="icon" title={t('preview.nextMatch')} onClick={() => caps.search!.next()}>
            <ChevronDown size={15} />
          </Button>
        </div>
      )}

      <div className="flex-1" />

      {caps.copy && (
        <Button variant="ghost" size="icon" title={copied ? t('preview.copied') : t('preview.copy')} onClick={doCopy}>
          {copied ? <Check size={15} /> : <Copy size={15} />}
        </Button>
      )}
      {caps.download && (
        <a href={caps.download.url} download={caps.download.filename} title={t('preview.download')}>
          <Button variant="ghost" size="icon">
            <Download size={15} />
          </Button>
        </a>
      )}
    </div>
  )
}
