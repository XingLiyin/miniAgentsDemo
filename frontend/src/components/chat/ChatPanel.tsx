import { useState, useRef, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Send, ChevronDown, ChevronRight, Wrench, Bot, User, Loader2, CheckCircle2, XCircle, Clock, MessageCircleQuestion, Eye, Code2, Paperclip, X } from 'lucide-react'
import { clsx } from 'clsx'
import { sessionsApi } from '@/api/sessions'
import type { ContentPart, ImagePart } from '@/api/sessions'
import { useSessionSSE } from '@/hooks/useSessionSSE'
import type { ChatItem, ChatMessage, ChatToolCall, ChatTaskEvent, ChatWaitingInput, ChatLLMPrompt, ChatObserverMessage, ChatObserverToolCall, ChatImageData } from '@/hooks/useSessionSSE'
import { formatTime } from '@/lib/status'
import { SessionStatusBadge } from '@/components/session/StatusBadge'
import { Spinner } from '@/components/ui/spinner'

// ── Individual item renderers ─────────────────────────────────────────────────

function imageUrl(img: ChatImageData): string {
  return img.source_type === 'url' ? img.data : `data:${img.media_type};base64,${img.data}`
}

function ImageGrid({ images }: { images: ChatImageData[] }) {
  return (
    <div className={clsx('grid gap-1 mt-1', images.length === 1 ? 'grid-cols-1' : 'grid-cols-2')}>
      {images.map((img, i) => (
        <a key={i} href={imageUrl(img)} target="_blank" rel="noopener noreferrer">
          <img
            src={imageUrl(img)}
            alt=""
            className="rounded-lg max-h-48 w-full object-cover cursor-pointer hover:opacity-90 transition-opacity"
          />
        </a>
      ))}
    </div>
  )
}

function ReasoningBlock({
  reasoning,
  tone = 'actor',
  defaultOpen = false,
}: {
  reasoning: string
  tone?: 'actor' | 'observer'
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const styles = tone === 'observer'
    ? {
        button: 'text-purple-500 hover:text-purple-700',
        panel: 'bg-purple-100/80 border-purple-200 text-purple-900',
        label: 'text-purple-700',
      }
    : {
        button: 'text-slate-500 hover:text-slate-700',
        panel: 'bg-slate-100 border-slate-200 text-slate-800',
        label: 'text-slate-700',
      }

  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen(o => !o)}
        className={clsx('flex items-center gap-1.5 text-xs transition-colors', styles.button)}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="font-medium">Reasoning</span>
      </button>
      {open && (
        <div className={clsx('mt-1 rounded-lg border px-3 py-2 text-xs leading-5', styles.panel)}>
          <p className={clsx('mb-1 font-medium', styles.label)}>Model reasoning</p>
          <p className="whitespace-pre-wrap break-words">{reasoning}</p>
        </div>
      )}
    </div>
  )
}

function MessageBubble({ item }: { item: ChatMessage }) {
  const isUser = item.role === 'user'
  return (
    <div className={clsx('flex items-start gap-2', isUser ? 'flex-row-reverse' : 'flex-row')}>
      <div className={clsx(
        'w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5',
        isUser ? 'bg-blue-500' : 'bg-gray-200'
      )}>
        {isUser
          ? <User size={12} className="text-white" />
          : <Bot size={12} className="text-gray-600" />}
      </div>
      <div className={clsx('flex flex-col max-w-[80%]', isUser ? 'items-end' : 'items-start')}>
        <div className={clsx(
          'rounded-xl px-3 py-2 text-sm leading-relaxed',
          isUser
            ? 'bg-blue-600 text-white rounded-tr-sm'
            : 'bg-white border border-gray-200 text-gray-800 rounded-tl-sm'
        )}>
          {item.images && item.images.length > 0 && <ImageGrid images={item.images} />}
          {!isUser && item.reasoning && <ReasoningBlock reasoning={item.reasoning} />}
          {item.content && <p className="whitespace-pre-wrap break-words mt-1">{item.content}</p>}
        </div>
        {item.created_at && (
          <p className="text-xs text-gray-400 mt-1 px-1">{formatTime(item.created_at)}</p>
        )}
      </div>
    </div>
  )
}

function ToolCallCard({ item }: { item: ChatToolCall }) {
  const [open, setOpen] = useState(false)
  const argStr = (() => {
    try { return JSON.stringify(item.arguments, null, 2) } catch { return String(item.arguments) }
  })()

  return (
    <div className="flex items-start gap-2 px-1">
      <div className="w-6 h-6 rounded bg-gray-100 flex items-center justify-center flex-shrink-0 mt-0.5">
        <Wrench size={11} className="text-gray-500" />
      </div>
      <div className="flex-1 min-w-0">
        <button
          onClick={() => setOpen(o => !o)}
          className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-700 transition-colors w-full text-left"
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <span className="font-mono font-medium text-gray-700">{item.tool_name}</span>
          {item.is_error
            ? <XCircle size={11} className="text-red-400 ml-auto flex-shrink-0" />
            : <CheckCircle2 size={11} className="text-green-400 ml-auto flex-shrink-0" />}
        </button>
        {open && (
          <div className="mt-1.5 bg-gray-900 rounded-lg overflow-hidden text-xs">
            {argStr !== '{}' && (
              <div className="px-3 py-2 border-b border-gray-700">
                <p className="text-gray-400 mb-1">参数</p>
                <pre className="text-yellow-300 overflow-x-auto whitespace-pre-wrap break-words">{argStr}</pre>
              </div>
            )}
            <div className="px-3 py-2">
              <p className="text-gray-400 mb-1">{item.is_error ? '错误' : '结果'}</p>
              <pre className={clsx(
                'overflow-x-auto whitespace-pre-wrap break-words',
                item.is_error ? 'text-red-400' : 'text-green-300'
              )}>{item.result || '(空)'}</pre>
            </div>
          </div>
        )}
        {item.created_at && (
          <p className="text-xs text-gray-400 mt-1">{formatTime(item.created_at)}</p>
        )}
      </div>
    </div>
  )
}

function TaskEventBadge({ item }: { item: ChatTaskEvent }) {
  const { task } = item
  const isCreated = task.status === 'PENDING'
  const isActive = task.status === 'ACTIVE'
  const isFinished = task.status === 'FINISHED'
  const isFailed = task.status === 'FAILED'


  return (
    <div className="flex justify-center">
      <div className={clsx(
        'flex items-center gap-1.5 text-xs px-3 py-1 rounded-full',
        isCreated ? 'bg-blue-50 text-blue-600' :
        isActive ? 'bg-blue-50 text-blue-600' :
        isFinished ? 'bg-green-50 text-green-700' :
        isFailed ? 'bg-red-50 text-red-600' :
        'bg-gray-100 text-gray-500'
      )}>
        {isActive && <Loader2 size={10} className="animate-spin" />}
        {isFinished && <CheckCircle2 size={10} />}
        {isFailed && <XCircle size={10} />}
        {(isCreated && !isActive) && <Clock size={10} />}
        <span className="font-medium truncate max-w-xs">{task.title}</span>
      </div>
    </div>
  )
}

function StreamingBubble({ text, images, reasoning }: { text: string; images?: ChatImageData[]; reasoning?: string }) {
  return (
    <div className="flex items-start gap-2">
      <div className="w-6 h-6 rounded-full bg-gray-200 flex items-center justify-center flex-shrink-0 mt-0.5">
        <Bot size={12} className="text-gray-600" />
      </div>
      <div className="flex flex-col items-start max-w-[80%]">
        <div className="rounded-xl px-3 py-2 text-sm leading-relaxed bg-white border border-gray-200 text-gray-800 rounded-tl-sm">
          {images && images.length > 0 && <ImageGrid images={images} />}
          {reasoning && <ReasoningBlock reasoning={reasoning} defaultOpen />}
          {(text || !reasoning) && (
            <p className="whitespace-pre-wrap break-words mt-1">
              {text}
              <span className="inline-block w-0.5 h-4 bg-gray-400 ml-0.5 align-text-bottom animate-pulse" />
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function LLMPromptCard({ item }: { item: ChatLLMPrompt }) {
  const [open, setOpen] = useState(false)
  const isObserver = item.source === 'observer'
  const label = isObserver
    ? `Observer prompt — ${item.round_label}`
    : `Actor prompt — ${item.round_label}`

  return (
    <div className="flex items-start gap-2 px-1">
      <div className="w-6 h-6 rounded bg-slate-100 flex items-center justify-center flex-shrink-0 mt-0.5">
        <Code2 size={11} className="text-slate-500" />
      </div>
      <div className="flex-1 min-w-0">
        <button
          onClick={() => setOpen(o => !o)}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-600 transition-colors w-full text-left"
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <span className="font-mono">{label}</span>
          {item.tool_names.length > 0 && (
            <span className="ml-auto text-slate-300 font-normal">tools: {item.tool_names.length}</span>
          )}
        </button>
        {open && (
          <div className="mt-1.5 bg-slate-900 rounded-lg overflow-hidden text-xs font-mono">
            {item.system_prompt && (
              <div className="px-3 py-2 border-b border-slate-700">
                <p className="text-slate-400 mb-1">system</p>
                <pre className="text-slate-200 whitespace-pre-wrap break-words">{item.system_prompt}</pre>
              </div>
            )}
            {item.messages.map((m, i) => (
              <div key={i} className="px-3 py-2 border-b border-slate-800 last:border-b-0">
                <p className="text-slate-400 mb-1">{m.role}</p>
                <pre className="text-slate-200 whitespace-pre-wrap break-words">
                  {typeof m.content === 'string' ? m.content : JSON.stringify(m.content, null, 2)}
                </pre>
              </div>
            ))}
            {item.tool_names.length > 0 && (
              <div className="px-3 py-2 border-t border-slate-700">
                <p className="text-slate-400 mb-1">tools</p>
                <p className="text-slate-300">{item.tool_names.join(', ')}</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function ObserverBubble({ item }: { item: ChatObserverMessage }) {
  const isRound2 = item.round_label.includes('round2')
  return (
    <div className="flex items-start gap-2">
      <div className="w-6 h-6 rounded-full bg-purple-100 flex items-center justify-center flex-shrink-0 mt-0.5">
        <Eye size={12} className="text-purple-600" />
      </div>
      <div className="flex flex-col items-start max-w-[80%]">
        <p className="text-xs text-purple-400 mb-0.5">{isRound2 ? 'Observer · 任务复核' : 'Observer · 评估'}</p>
        <div className="rounded-xl px-3 py-2 text-sm leading-relaxed bg-purple-50 border border-purple-200 text-purple-900 rounded-tl-sm">
          {item.reasoning && <ReasoningBlock reasoning={item.reasoning} tone="observer" />}
          <p className="whitespace-pre-wrap break-words">{item.content}</p>
        </div>
        {item.created_at && (
          <p className="text-xs text-gray-400 mt-1 px-1">{formatTime(item.created_at)}</p>
        )}
      </div>
    </div>
  )
}

function ObserverToolCallCard({ item }: { item: ChatObserverToolCall }) {
  const [open, setOpen] = useState(false)
  const argStr = (() => {
    try { return JSON.stringify(item.arguments, null, 2) } catch { return String(item.arguments) }
  })()

  return (
    <div className="flex items-start gap-2 px-1">
      <div className="w-6 h-6 rounded bg-purple-100 flex items-center justify-center flex-shrink-0 mt-0.5">
        <Wrench size={11} className="text-purple-500" />
      </div>
      <div className="flex-1 min-w-0">
        <button
          onClick={() => setOpen(o => !o)}
          className="flex items-center gap-1.5 text-xs text-purple-400 hover:text-purple-600 transition-colors w-full text-left"
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <span className="font-mono font-medium text-purple-700">{item.tool_name}</span>
          {item.is_error
            ? <XCircle size={11} className="text-red-400 ml-auto flex-shrink-0" />
            : <CheckCircle2 size={11} className="text-purple-400 ml-auto flex-shrink-0" />}
        </button>
        {open && (
          <div className="mt-1.5 bg-purple-950 rounded-lg overflow-hidden text-xs">
            {argStr !== '{}' && (
              <div className="px-3 py-2 border-b border-purple-800">
                <p className="text-purple-400 mb-1">参数</p>
                <pre className="text-yellow-300 overflow-x-auto whitespace-pre-wrap break-words">{argStr}</pre>
              </div>
            )}
            <div className="px-3 py-2">
              <p className="text-purple-400 mb-1">{item.is_error ? '错误' : '结果'}</p>
              <pre className={clsx(
                'overflow-x-auto whitespace-pre-wrap break-words',
                item.is_error ? 'text-red-400' : 'text-purple-200'
              )}>{item.result || '(空)'}</pre>
            </div>
          </div>
        )}
        {item.created_at && (
          <p className="text-xs text-purple-300 mt-1">{formatTime(item.created_at)}</p>
        )}
      </div>
    </div>
  )
}

function ObserverStreamingBubble({ text, reasoning }: { text: string; reasoning?: string }) {
  return (
    <div className="flex items-start gap-2">
      <div className="w-6 h-6 rounded-full bg-purple-100 flex items-center justify-center flex-shrink-0 mt-0.5">
        <Eye size={12} className="text-purple-600" />
      </div>
      <div className="flex flex-col items-start max-w-[80%]">
        <p className="text-xs text-purple-400 mb-0.5">Observer</p>
        <div className="rounded-xl px-3 py-2 text-sm leading-relaxed bg-purple-50 border border-purple-200 text-purple-900 rounded-tl-sm">
          {reasoning && <ReasoningBlock reasoning={reasoning} tone="observer" defaultOpen />}
          {(text || !reasoning) && (
            <p className="whitespace-pre-wrap break-words">
              {text}
              <span className="inline-block w-0.5 h-4 bg-purple-400 ml-0.5 align-text-bottom animate-pulse" />
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function ChatItemView({ item }: { item: ChatItem }) {
  switch (item.kind) {
    case 'message': return <MessageBubble item={item} />
    case 'tool_call': return <ToolCallCard item={item} />
    case 'task_event': return <TaskEventBadge item={item} />
    case 'waiting_input': return null // handled by input area
    case 'llm_prompt': return <LLMPromptCard item={item} />
    case 'observer_message': return <ObserverBubble item={item} />
    case 'observer_tool_call': return <ObserverToolCallCard item={item} />
  }
}

// ── Input area ────────────────────────────────────────────────────────────────

interface Attachment {
  file: File
  dataUrl: string   // base64 data URL for preview + sending
  mediaType: string
}

function AttachmentPreview({ attachments, onRemove }: { attachments: Attachment[]; onRemove: (i: number) => void }) {
  if (attachments.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5 mb-2">
      {attachments.map((a, i) => (
        <div key={i} className="relative group w-14 h-14 rounded-lg overflow-hidden border border-gray-200 flex-shrink-0">
          <img src={a.dataUrl} alt="" className="w-full h-full object-cover" />
          <button
            onClick={() => onRemove(i)}
            className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <X size={14} className="text-white" />
          </button>
        </div>
      ))}
    </div>
  )
}

function TextInput({
  sessionId,
  disabled,
}: {
  sessionId: string
  disabled?: boolean
}) {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (content: string | ContentPart[]) => sessionsApi.sendMessage(sessionId, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
      setText('')
      setAttachments([])
      textareaRef.current?.focus()
    },
  })

  const submit = () => {
    const trimmed = text.trim()
    if ((!trimmed && attachments.length === 0) || mutation.isPending || disabled) return

    if (attachments.length > 0) {
      const parts: ContentPart[] = [
        ...attachments.map((a): ImagePart => ({
          type: 'image',
          data: a.dataUrl.split(',')[1],   // strip "data:...;base64," prefix
          media_type: a.mediaType,
          source_type: 'base64',
        })),
        ...(trimmed ? [{ type: 'text' as const, text: trimmed }] : []),
      ]
      mutation.mutate(parts)
    } else {
      mutation.mutate(trimmed)
    }
  }

  const handleFiles = (files: FileList | null) => {
    if (!files) return
    Array.from(files).forEach(file => {
      if (!file.type.startsWith('image/')) return
      const reader = new FileReader()
      reader.onload = e => {
        const dataUrl = e.target?.result as string
        setAttachments(prev => [...prev, { file, dataUrl, mediaType: file.type }])
      }
      reader.readAsDataURL(file)
    })
  }

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }, [text])

  const canSend = (text.trim() || attachments.length > 0) && !mutation.isPending && !disabled

  return (
    <div className="border-t border-gray-200 bg-white p-3">
      {mutation.isError && <p className="text-xs text-red-500 mb-2">发送失败，请重试</p>}
      <AttachmentPreview attachments={attachments} onRemove={i => setAttachments(prev => prev.filter((_, idx) => idx !== i))} />
      <div className="flex items-end gap-2">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={e => handleFiles(e.target.files)}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled}
          className="flex items-center justify-center w-8 h-8 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors flex-shrink-0 disabled:opacity-40"
          title="附加图片"
        >
          <Paperclip size={15} />
        </button>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
          onPaste={e => {
            const items = e.clipboardData?.items
            if (!items) return
            const imageItems = Array.from(items).filter(it => it.type.startsWith('image/'))
            if (imageItems.length === 0) return
            e.preventDefault()
            imageItems.forEach(it => {
              const file = it.getAsFile()
              if (file) handleFiles(Object.assign(new DataTransfer(), { files: [file] as unknown as FileList }).files)
            })
          }}
          placeholder={disabled ? 'Agent 正在运行中...' : '向 Agent 发送消息… (Enter 发送，Shift+Enter 换行)'}
          rows={1}
          disabled={disabled}
          className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 leading-5 disabled:bg-gray-50 disabled:text-gray-400"
        />
        <button
          onClick={submit}
          disabled={!canSend}
          className={clsx(
            'flex items-center justify-center w-8 h-8 rounded-lg transition-colors flex-shrink-0',
            canSend ? 'bg-blue-500 text-white hover:bg-blue-600' : 'bg-gray-100 text-gray-400 cursor-not-allowed'
          )}
        >
          {mutation.isPending ? <Spinner size="sm" /> : <Send size={14} />}
        </button>
      </div>
    </div>
  )
}

function WaitingInputArea({
  sessionId,
  waitingInput,
}: {
  sessionId: string
  waitingInput: ChatWaitingInput
}) {
  const [text, setText] = useState('')
  const [rejected, setRejected] = useState(false)
  const [feedback, setFeedback] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const queryClient = useQueryClient()

  const isTaskConfirm = waitingInput.input_type === 'task_completion_confirm'

  const mutation = useMutation({
    mutationFn: (content: string) =>
      sessionsApi.answerInput(sessionId, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
      setText('')
    },
  })

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }, [text, feedback])

  if (isTaskConfirm) {
    return (
      <div className="border-t border-amber-200 bg-amber-50 p-3">
        <div className="flex items-start gap-2 mb-3">
          <MessageCircleQuestion size={15} className="text-amber-600 mt-0.5 flex-shrink-0" />
          <div>
            <p className="text-sm text-amber-800 font-medium">请确认任务是否完成</p>
            <p className="text-xs text-amber-700 mt-0.5 truncate">任务：{waitingInput.task_title}</p>
          </div>
        </div>
        {mutation.isError && <p className="text-xs text-red-500 mb-2">提交失败，请重试</p>}
        {!rejected ? (
          <div className="flex gap-2">
            <button
              onClick={() => mutation.mutate('用户已确认任务完成。')}
              disabled={mutation.isPending}
              className="flex-1 rounded-lg bg-green-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-600 disabled:opacity-50 transition-colors"
            >
              {mutation.isPending ? <Spinner size="sm" /> : '已完成'}
            </button>
            <button
              onClick={() => setRejected(true)}
              disabled={mutation.isPending}
              className="flex-1 rounded-lg bg-red-50 border border-red-200 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-100 disabled:opacity-50 transition-colors"
            >
              未完成，需重试
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <textarea
              ref={textareaRef}
              value={feedback}
              onChange={e => setFeedback(e.target.value)}
              placeholder="（可选）补充说明… (Enter 提交)"
              rows={1}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  const msg = feedback.trim()
                    ? `用户表示任务未完成，请重试。用户补充说明：${feedback.trim()}`
                    : '用户表示任务未完成，请重试。'
                  mutation.mutate(msg)
                }
              }}
              className="resize-none rounded-lg border border-red-200 bg-white px-3 py-2 text-sm outline-none focus:border-red-400 focus:ring-1 focus:ring-red-300 leading-5"
            />
            <div className="flex gap-2">
              <button onClick={() => setRejected(false)} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">返回</button>
              <button
                onClick={() => {
                  const msg = feedback.trim()
                    ? `用户表示任务未完成，请重试。用户补充说明：${feedback.trim()}`
                    : '用户表示任务未完成，请重试。'
                  mutation.mutate(msg)
                }}
                disabled={mutation.isPending}
                className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-red-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50 transition-colors"
              >
                {mutation.isPending ? <Spinner size="sm" /> : <><Send size={12} />提交</>}
              </button>
            </div>
          </div>
        )}
      </div>
    )
  }

  // Normal user input
  return (
    <div className="border-t border-amber-200 bg-amber-50 p-3">
      <div className="flex items-start gap-2 mb-2">
        <MessageCircleQuestion size={15} className="text-amber-600 mt-0.5 flex-shrink-0" />
        <p className="text-sm text-amber-800 font-medium">{waitingInput.prompt || '请输入您的回复'}</p>
      </div>
      {mutation.isError && <p className="text-xs text-red-500 mb-2">提交失败，请重试</p>}
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); mutation.mutate(text.trim()) } }}
          placeholder="输入您的回复… (Enter 提交，Shift+Enter 换行)"
          rows={1}
          className="flex-1 resize-none rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm outline-none focus:border-amber-500 focus:ring-1 focus:ring-amber-400 leading-5"
        />
        <button
          onClick={() => { if (text.trim()) mutation.mutate(text.trim()) }}
          disabled={!text.trim() || mutation.isPending}
          className={clsx(
            'flex items-center justify-center w-8 h-8 rounded-lg transition-colors flex-shrink-0',
            text.trim() && !mutation.isPending
              ? 'bg-amber-500 text-white hover:bg-amber-600'
              : 'bg-gray-100 text-gray-400 cursor-not-allowed'
          )}
        >
          {mutation.isPending ? <Spinner size="sm" /> : <Send size={14} />}
        </button>
      </div>
    </div>
  )
}

// ── Main ChatPanel ────────────────────────────────────────────────────────────

interface ChatPanelProps {
  sessionId: string
}

export function ChatPanel({ sessionId }: ChatPanelProps) {
  const {
    session,
    items,
    waitingInput,
    streamingText,
    streamingReasoning,
    streamingImages,
    observerStreamingText,
    observerStreamingReasoning,
    connected,
  } = useSessionSSE(sessionId)
  const bottomRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new items or streaming updates
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [items.length, streamingText, streamingReasoning, observerStreamingText, observerStreamingReasoning])

  const isRunning = session?.status === 'RUNNING' || session?.status === 'QUEUED'
  const isTerminal = session?.status && ['SUCCEEDED', 'FAILED', 'CANCELED'].includes(session.status)

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between gap-3 flex-shrink-0">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 truncate">{session?.user_prompt || '加载中…'}</p>
          <div className="flex items-center gap-2 mt-0.5">
            {session && <SessionStatusBadge status={session.status} />}
            {session && session.token_used > 0 && (
              <span className="text-xs text-gray-400">
                {(session.token_used / 1000).toFixed(1)}k / {(session.token_budget / 1000).toFixed(0)}k tokens
              </span>
            )}
            {!connected && (
              <span className="text-xs text-orange-500 flex items-center gap-1">
                <Loader2 size={10} className="animate-spin" />
                连接中
              </span>
            )}
          </div>
        </div>
        {isRunning && (
          <div className="flex items-center gap-1.5 text-xs text-blue-600 flex-shrink-0">
            <Loader2 size={12} className="animate-spin" />
            <span>运行中</span>
          </div>
        )}
      </div>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {!session ? (
          <div className="flex justify-center py-12">
            <Spinner />
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-2">
            <Bot size={32} />
            <p className="text-sm">等待 Agent 开始工作…</p>
          </div>
        ) : (
          items.map(item => (
            <ChatItemView key={item.id} item={item} />
          ))
        )}

        {/* Actor streaming bubble */}
        {(streamingText || streamingReasoning || (streamingImages?.length ?? 0) > 0) && (
          <StreamingBubble text={streamingText ?? ''} reasoning={streamingReasoning ?? undefined} images={streamingImages ?? []} />
        )}

        {/* Observer streaming bubble */}
        {(observerStreamingText || observerStreamingReasoning) && (
          <ObserverStreamingBubble text={observerStreamingText ?? ''} reasoning={observerStreamingReasoning ?? undefined} />
        )}

        {/* Running indicator (only when not streaming) */}
        {isRunning && !streamingText && !streamingReasoning && !observerStreamingText && !observerStreamingReasoning && items.length > 0 && (
          <div className="flex items-center gap-2 text-xs text-gray-400 px-2">
            <Loader2 size={11} className="animate-spin" />
            <span>Agent 正在处理…</span>
          </div>
        )}

        {/* Terminal status */}
        {isTerminal && session && (
          <div className={clsx(
            'flex justify-center',
          )}>
            <div className={clsx(
              'flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full',
              session.status === 'SUCCEEDED' ? 'bg-green-50 text-green-700' :
              session.status === 'FAILED' ? 'bg-red-50 text-red-600' :
              'bg-gray-100 text-gray-500'
            )}>
              {session.status === 'SUCCEEDED' && <CheckCircle2 size={11} />}
              {session.status === 'FAILED' && <XCircle size={11} />}
              <span>
                {session.status === 'SUCCEEDED' ? '会话已完成' :
                 session.status === 'FAILED' ? '会话失败' : '会话已取消'}
              </span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      {waitingInput ? (
        <WaitingInputArea sessionId={sessionId} waitingInput={waitingInput} />
      ) : (
        <TextInput sessionId={sessionId} disabled={isRunning} />
      )}
    </div>
  )
}
