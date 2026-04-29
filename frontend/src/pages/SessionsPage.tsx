import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Inbox, Send, Bot } from 'lucide-react'
import { sessionsApi } from '@/api/sessions'
import type { SessionStatus, SessionConfig } from '@/types'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { SessionCard } from '@/components/session/SessionCard'
import { CreateSessionDialog } from '@/components/session/CreateSessionDialog'
import { ChatPanel } from '@/components/chat/ChatPanel'
import { clsx } from 'clsx'

type FilterStatus = 'ALL' | SessionStatus

const FILTERS: { label: string; value: FilterStatus }[] = [
  { label: '全部', value: 'ALL' },
  { label: '运行中', value: 'RUNNING' },
  { label: '等待输入', value: 'WAITING_INPUT' },
  { label: '已完成', value: 'SUCCEEDED' },
  { label: '失败', value: 'FAILED' },
]

// ── NewSessionPanel ────────────────────────────────────────────────────────────

interface NewSessionPanelProps {
  config: SessionConfig
  onCreated: (sessionId: string) => void
  onCancel: () => void
}

function NewSessionPanel({ config, onCreated, onCancel }: NewSessionPanelProps) {
  const queryClient = useQueryClient()
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }, [text])

  const mutation = useMutation({
    mutationFn: (userPrompt: string) =>
      sessionsApi.create({ ...config, user_prompt: userPrompt }),
    onSuccess: (session) => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
      onCreated(session.id)
    },
  })

  function submit() {
    const trimmed = text.trim()
    if (!trimmed || mutation.isPending) return
    mutation.mutate(trimmed)
    setText('')
  }

  const canSend = text.trim() && !mutation.isPending

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between gap-3 flex-shrink-0">
        <p className="text-sm font-medium text-gray-500">新建 Session — 发送第一条消息以开始</p>
        <button
          onClick={onCancel}
          className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
        >
          取消
        </button>
      </div>

      {/* Empty body */}
      <div className="flex-1 flex flex-col items-center justify-center text-gray-400 gap-3">
        <Bot size={36} className="text-gray-300" />
        <p className="text-sm">在下方输入你的第一条消息，Agent 将立即开始工作</p>
        {mutation.isError && (
          <p className="text-xs text-red-500">创建失败，请重试</p>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-gray-200 bg-white p-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={e => setText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
            placeholder="向 Agent 发送第一条消息… (Enter 发送，Shift+Enter 换行)"
            rows={1}
            className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 leading-5"
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
    </div>
  )
}

// ── SessionsPage ──────────────────────────────────────────────────────────────

export function SessionsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pendingConfig, setPendingConfig] = useState<SessionConfig | null>(null)
  const [filter, setFilter] = useState<FilterStatus>('ALL')
  const [showCreate, setShowCreate] = useState(false)

  const { data: sessions = [], isLoading } = useQuery({
    queryKey: ['sessions'],
    queryFn: sessionsApi.list,
    refetchInterval: 5000,
  })

  const filtered = filter === 'ALL'
    ? sessions
    : sessions.filter((s) => s.status === filter)

  function handleConfigured(config: SessionConfig) {
    setPendingConfig(config)
    setSelectedId(null)
  }

  function handleSessionCreated(sessionId: string) {
    setPendingConfig(null)
    setSelectedId(sessionId)
  }

  return (
    <div className="flex h-full">
      {/* Left panel — session list */}
      <div className="w-72 flex-shrink-0 border-r border-gray-200 bg-white flex flex-col">
        {/* Header */}
        <div className="px-4 py-3 border-b border-gray-100">
          <div className="flex items-center justify-between mb-2.5">
            <h1 className="text-sm font-semibold text-gray-900">Sessions</h1>
            <Button size="sm" onClick={() => setShowCreate(true)}>
              <Plus size={13} />
              新建
            </Button>
          </div>
          {/* Filter pills */}
          <div className="flex gap-1 flex-wrap">
            {FILTERS.map((f) => (
              <button
                key={f.value}
                onClick={() => setFilter(f.value)}
                className={clsx(
                  'px-2 py-0.5 rounded text-xs transition-colors',
                  filter === f.value
                    ? 'bg-blue-100 text-blue-700 font-medium'
                    : 'text-gray-500 hover:bg-gray-100'
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-gray-400">
              <Inbox size={24} />
              <p className="text-sm">暂无 Session</p>
              <Button size="sm" variant="secondary" onClick={() => setShowCreate(true)}>
                创建第一个
              </Button>
            </div>
          ) : (
            filtered.map((s) => (
              <SessionCard
                key={s.id}
                session={s}
                selected={selectedId === s.id}
                onClick={() => { setSelectedId(s.id); setPendingConfig(null) }}
                onDeleted={() => { if (selectedId === s.id) setSelectedId(null) }}
              />
            ))
          )}
        </div>
      </div>

      {/* Right panel — chat */}
      <div className="flex-1 overflow-hidden bg-gray-50">
        {pendingConfig ? (
          <NewSessionPanel
            config={pendingConfig}
            onCreated={handleSessionCreated}
            onCancel={() => setPendingConfig(null)}
          />
        ) : selectedId ? (
          <ChatPanel key={selectedId} sessionId={selectedId} />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-3">
            <Inbox size={32} />
            <p className="text-sm">选择一个 Session 查看对话</p>
            <Button variant="secondary" onClick={() => setShowCreate(true)}>
              <Plus size={14} />
              新建 Session
            </Button>
          </div>
        )}
      </div>

      <CreateSessionDialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onConfigured={handleConfigured}
      />
    </div>
  )
}
