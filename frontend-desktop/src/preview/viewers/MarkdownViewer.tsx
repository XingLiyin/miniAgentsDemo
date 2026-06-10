import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import { useFileText, rawUrl, Loading, ErrorMsg } from './common'

function resolveImgSrc(src: string, mdPath: string): string {
  if (!src || /^(https?:|data:|\/\/)/i.test(src)) return src
  if (src.startsWith('/')) return rawUrl(src)
  const dir = mdPath.replace(/\\/g, '/').split('/').slice(0, -1).join('/')
  return rawUrl(dir ? `${dir}/${src}` : src)
}

export function MarkdownViewer({ path, filename }: { path: string; filename: string }) {
  const { content, error } = useFileText(path)
  usePreviewToolbar({
    copy: () => content ?? '',
    download: { url: rawUrl(path), filename },
  }, [content, path, filename])

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
