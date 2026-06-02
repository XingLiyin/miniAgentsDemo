import { useEffect, useState } from 'react'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'

export function DocxViewer({ path, filename }: { path: string; filename: string }) {
  const [html, setHtml] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  usePreviewToolbar({ download: { url: rawUrl(path), filename } }, [path, filename])

  useEffect(() => {
    setHtml(null); setError(null)
    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then((buf) => import('mammoth').then((m) => m.convertToHtml({ arrayBuffer: buf })))
      .then((result) => setHtml(result.value))
      .catch((e) => setError(String(e)))
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
