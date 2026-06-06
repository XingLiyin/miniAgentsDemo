import { useEffect, useState } from 'react'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'

export function DocxViewer({ path, filename }: { path: string; filename: string }) {
  const [html, setHtml] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  usePreviewToolbar({ download: { url: rawUrl(path), filename } }, [path, filename])

  useEffect(() => {
    let cancelled = false
    setHtml(null); setError(null)
    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then((buf) => import('mammoth').then((m) => m.convertToHtml({ arrayBuffer: buf })))
      .then((result) => { if (!cancelled) setHtml(result.value) })
      .catch((e) => { if (!cancelled) setError(String(e)) })
    // Ignore a stale resolution if `path` changed before mammoth finished.
    return () => { cancelled = true }
  }, [path])

  if (error) return <ErrorMsg msg={error} />
  if (html === null) return <Loading />
  return (
    <div
      className="prose prose-sm max-w-none px-10 py-6 [&_p:empty]:hidden"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
