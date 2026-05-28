import { useEffect, useState } from 'react'
import SyntaxHighlighter from 'react-syntax-highlighter/dist/esm/prism-light'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python'
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript'
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript'
import jsx from 'react-syntax-highlighter/dist/esm/languages/prism/jsx'
import tsx from 'react-syntax-highlighter/dist/esm/languages/prism/tsx'
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash'
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json'
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml'
import toml from 'react-syntax-highlighter/dist/esm/languages/prism/toml'
import css from 'react-syntax-highlighter/dist/esm/languages/prism/css'
import go from 'react-syntax-highlighter/dist/esm/languages/prism/go'
import rust from 'react-syntax-highlighter/dist/esm/languages/prism/rust'
import java from 'react-syntax-highlighter/dist/esm/languages/prism/java'
import cpp from 'react-syntax-highlighter/dist/esm/languages/prism/cpp'

SyntaxHighlighter.registerLanguage('python', python)
SyntaxHighlighter.registerLanguage('javascript', javascript)
SyntaxHighlighter.registerLanguage('typescript', typescript)
SyntaxHighlighter.registerLanguage('jsx', jsx)
SyntaxHighlighter.registerLanguage('tsx', tsx)
SyntaxHighlighter.registerLanguage('bash', bash)
SyntaxHighlighter.registerLanguage('json', json)
SyntaxHighlighter.registerLanguage('yaml', yaml)
SyntaxHighlighter.registerLanguage('toml', toml)
SyntaxHighlighter.registerLanguage('css', css)
SyntaxHighlighter.registerLanguage('go', go)
SyntaxHighlighter.registerLanguage('rust', rust)
SyntaxHighlighter.registerLanguage('java', java)
SyntaxHighlighter.registerLanguage('cpp', cpp)
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { XIcon, FileIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { useI18n } from '@/i18n'

const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'ico', 'svg'])
const MD_EXTS = new Set(['md', 'markdown'])
const DOCX_EXTS = new Set(['docx'])
const EXCEL_EXTS = new Set(['xlsx', 'xls', 'csv'])
const CODE_EXTS: Record<string, string> = {
  py: 'python', js: 'javascript', ts: 'typescript',
  jsx: 'jsx', tsx: 'tsx', sh: 'bash', bash: 'bash',
  json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml',
  css: 'css', scss: 'css', go: 'go', rs: 'rust',
  java: 'java', cpp: 'cpp', c: 'cpp', kt: 'typescript',
}
const TEXT_EXTS = new Set(['txt', 'log', 'rst', 'xml', 'html', 'htm', 'less', 'vue', 'php', 'rb', 'swift'])

function getExt(path: string) {
  const name = path.split(/[/\\]/).pop() ?? path
  return name.includes('.') ? (name.split('.').pop()?.toLowerCase() ?? '') : ''
}

function rawUrl(path: string) {
  return `/api/v1/workspace/file/raw?path=${encodeURIComponent(path)}`
}

function fileType(ext: string): 'image' | 'markdown' | 'docx' | 'excel' | 'code' | 'text' | 'binary' {
  if (IMAGE_EXTS.has(ext)) return 'image'
  if (MD_EXTS.has(ext)) return 'markdown'
  if (DOCX_EXTS.has(ext)) return 'docx'
  if (EXCEL_EXTS.has(ext)) return 'excel'
  if (ext in CODE_EXTS) return 'code'
  if (TEXT_EXTS.has(ext)) return 'text'
  return 'binary'
}

interface Props {
  path: string
  onClose: () => void
}

export function FilePreviewModal({ path, onClose }: Props) {
  const { t } = useI18n()
  const ext = getExt(path)
  const type = fileType(ext)
  const name = path.split(/[/\\]/).pop() ?? path

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm"
      style={{ background: 'rgba(15,31,61,.35)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="relative flex flex-col rounded-xl"
        style={{ width: '82vw', height: '85vh', maxWidth: 1200, background: 'var(--bg1)', boxShadow: '0 24px 80px rgba(15,31,61,.22)' }}>
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-3 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <FileIcon size={15} className="flex-shrink-0" style={{ color: 'var(--t3)' }} />
          <span className="min-w-0 flex-1 truncate text-sm font-medium" style={{ color: 'var(--t2)' }}>{name}</span>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <XIcon size={16} />
          </Button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto">
          {type === 'image' && <ImageViewer path={path} />}
          {type === 'markdown' && <MarkdownViewer path={path} />}
          {type === 'docx' && <DocxViewer path={path} />}
          {type === 'excel' && <ExcelViewer path={path} />}
          {type === 'code' && <CodeViewer path={path} lang={CODE_EXTS[ext]} />}
          {type === 'text' && <TextViewer path={path} />}
          {type === 'binary' && (
            <div className="flex h-full items-center justify-center text-sm" style={{ color: 'var(--t3)' }}>
              {t('filePreview.unsupported', { ext: ext || t('filePreview.unknownExt') })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ImageViewer({ path }: { path: string }) {
  return (
    <div className="flex h-full items-center justify-center p-4" style={{ background: 'var(--bg2)' }}>
      <img
        src={rawUrl(path)}
        alt={path}
        className="max-h-full max-w-full object-contain rounded shadow"
        style={{ imageRendering: path.endsWith('.svg') ? 'auto' : undefined }}
      />
    </div>
  )
}

function resolveImgSrc(src: string, mdPath: string): string {
  if (!src || /^(https?:|data:|\/\/)/i.test(src)) return src
  // absolute path within workspace
  if (src.startsWith('/')) return rawUrl(src)
  // relative to the md file's directory
  const dir = mdPath.replace(/\\/g, '/').split('/').slice(0, -1).join('/')
  const resolved = dir ? `${dir}/${src}` : src
  return rawUrl(resolved)
}

function MarkdownViewer({ path }: { path: string }) {
  const { content, error } = useFileText(path)

  if (error) return <ErrorMsg msg={error} />
  if (content === null) return <Loading />

  return (
    <div className="prose prose-sm max-w-none px-8 py-6">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img({ src, alt, ...rest }) {
            return <img src={resolveImgSrc(src ?? '', path)} alt={alt ?? ''} {...rest} className="max-w-full rounded" />
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function useFileText(path: string) {
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setContent(null); setError(null)
    fetch(`/api/v1/workspace/file?path=${encodeURIComponent(path)}`)
      .then(r => r.json())
      .then(d => setContent(d.content))
      .catch(e => setError(String(e)))
  }, [path])
  return { content, error }
}

function CodeViewer({ path, lang }: { path: string; lang: string }) {
  const { content, error } = useFileText(path)
  if (error) return <ErrorMsg msg={error} />
  if (content === null) return <Loading />
  return (
    <SyntaxHighlighter
      language={lang}
      style={oneLight}
      showLineNumbers
      customStyle={{ margin: 0, borderRadius: 0, fontSize: '12px', height: '100%' }}
    >
      {content}
    </SyntaxHighlighter>
  )
}

function TextViewer({ path }: { path: string }) {
  const { content, error } = useFileText(path)
  if (error) return <ErrorMsg msg={error} />
  if (content === null) return <Loading />
  return (
    <pre className="px-6 py-4 text-xs leading-relaxed whitespace-pre font-mono" style={{ color: 'var(--t1)' }}>
      {content}
    </pre>
  )
}

function DocxViewer({ path }: { path: string }) {
  const [html, setHtml] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setHtml(null); setError(null)
    fetch(rawUrl(path))
      .then(r => r.arrayBuffer())
      .then(buf => import('mammoth').then(m => m.convertToHtml({ arrayBuffer: buf })))
      .then(result => setHtml(result.value))
      .catch(e => setError(String(e)))
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

function ExcelViewer({ path }: { path: string }) {
  const { t } = useI18n()
  const [tables, setTables] = useState<{ name: string; rows: string[][] }[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [activeSheet, setActiveSheet] = useState(0)

  useEffect(() => {
    setTables(null); setError(null); setActiveSheet(0)
    const isCsv = path.toLowerCase().endsWith('.csv')
    const load = isCsv
      ? fetch(`/api/v1/workspace/file?path=${encodeURIComponent(path)}`).then(r => r.json()).then(d =>
          import('xlsx').then(XLSX => {
            const wb = XLSX.read(d.content, { type: 'string' })
            return wb.SheetNames.map((name: string) => {
              const ws = wb.Sheets[name]
              const rows = XLSX.utils.sheet_to_json<string[]>(ws, { header: 1, defval: '' })
              return { name, rows: rows as string[][] }
            })
          })
        )
      : fetch(rawUrl(path)).then(r => r.arrayBuffer()).then(buf =>
          import('xlsx').then(XLSX => {
            const wb = XLSX.read(buf, { type: 'array' })
            return wb.SheetNames.map((name: string) => {
              const ws = wb.Sheets[name]
              const rows = XLSX.utils.sheet_to_json<string[]>(ws, { header: 1, defval: '' })
              return { name, rows: rows as string[][] }
            })
          })
        )
    load.then(setTables).catch(e => setError(String(e)))
  }, [path])

  if (error) return <ErrorMsg msg={error} />
  if (tables === null) return <Loading />
  if (tables.length === 0) return <div className="p-6 text-sm" style={{ color: 'var(--t3)' }}>{t('filePreview.empty')}</div>

  const sheet = tables[activeSheet]

  return (
    <div className="flex flex-col h-full">
      {/* Sheet tabs */}
      {tables.length > 1 && (
        <div className="flex gap-1 px-4 pt-2 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          {tables.map((t, i) => (
            <button
              key={t.name}
              onClick={() => setActiveSheet(i)}
              className="px-3 py-1.5 text-xs rounded-t border-b-2 transition-colors"
              style={{
                borderBottomColor: i === activeSheet ? 'var(--blue)' : 'transparent',
                color: i === activeSheet ? 'var(--blue)' : 'var(--t2)',
                background: i === activeSheet ? 'var(--blue-dim)' : 'transparent',
                border: 'none', borderBottom: `2px solid ${i === activeSheet ? 'var(--blue)' : 'transparent'}`,
                cursor: 'pointer',
              }}
            >
              {t.name}
            </button>
          ))}
        </div>
      )}
      {/* Table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs border-collapse">
          <tbody>
            {sheet.rows.map((row, ri) => (
              <tr key={ri} style={ri === 0 ? { background: 'var(--bg2)', fontWeight: 600 } : undefined}>
                {row.map((cell, ci) => (
                  <td key={ci} className="px-2 py-1 whitespace-nowrap max-w-[200px] truncate" style={{ border: '1px solid var(--border)', color: 'var(--t1)' }}>
                    {String(cell ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Loading() {
  const { t } = useI18n()
  return (
    <div className="flex h-full items-center justify-center gap-2" style={{ color: 'var(--t3)' }}>
      <Spinner className="h-4 w-4" /> <span className="text-sm">{t('common.loading')}</span>
    </div>
  )
}

function ErrorMsg({ msg }: { msg: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-red-500 text-sm">{msg}</div>
  )
}
