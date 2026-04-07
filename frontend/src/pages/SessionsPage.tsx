import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Inbox, Send, MessageCircleQuestion } from 'lucide-react'
import { sessionsApi } from '@/api/sessions'
import type { Session, SessionStatus, Task } from '@/types'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { SessionCard } from '@/components/session/SessionCard'
import { SessionStats } from '@/components/session/SessionStats'
import { TaskTimeline } from '@/components/task/TaskTimeline'
import { MessageList } from '@/components/memory/MessageList'
import { ToolCallsTable } from '@/components/session/ToolCallsTable'
import { CreateSessionDialog } from '@/components/session/CreateSessionDialog'
import {
  useSession,
  useSessionTasks,
  useSessionMessages,
  useSessionToolCalls,
} from '@/hooks/useSessionPolling'
import { clsx } from 'clsx'

type FilterStatus = 'ALL' | SessionStatus

const FILTERS: { label: string; value: FilterStatus }[] = [
  { label: '全部', value: 'ALL' },
  { label: '运行中', value: 'RUNNING' },
  { label: '等待输入', value: 'WAITING_INPUT' },
  { label: '已完成', value: 'SUCCEEDED' },
  { label: '失败', value: 'FAILED' },
]

type DetailTab = 'tasks' | 'messages' | 'toolcalls'

function ChatInput({ sessionId }: { sessionId: string }) {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (content: string) => sessionsApi.sendMessage(sessionId, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
      queryClient.invalidateQueries({ queryKey: ['session-messages', sessionId] })
      setText('')
      textareaRef.current?.focus()
    },
  })

  const submit = () => {
    const trimmed = text.trim()
    if (!trimmed || mutation.isPending) return
    mutation.mutate(trimmed)
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }, [text])

  return (
    <div className="border-t border-gray-200 bg-white p-3">
      {mutation.isError && (
        <p className="text-xs text-red-500 mb-2">发送失败，请重试</p>
      )}
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="向 Agent 发送消息… (Enter 发送，Shift+Enter 换行)"
          rows={1}
          className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 leading-5"
        />
        <button
          onClick={submit}
          disabled={!text.trim() || mutation.isPending}
          className={clsx(
            'flex items-center justify-center w-8 h-8 rounded-lg transition-colors flex-shrink-0',
            text.trim() && !mutation.isPending
              ? 'bg-blue-500 text-white hover:bg-blue-600'
              : 'bg-gray-100 text-gray-400 cursor-not-allowed'
          )}
        >
          {mutation.isPending ? <Spinner size="sm" /> : <Send size={14} />}
        </button>
      </div>
    </div>
  )
}

function TaskCompletionConfirm({
  session,
  activeTask,
}: {
  session: Session
  activeTask: Task
}) {
  const [rejected, setRejected] = useState(false)
  const [feedback, setFeedback] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const queryClient = useQueryClient()

  const inputs = activeTask.inputs as Record<string, string>
  const taskTitle = inputs.task_title || activeTask.description || activeTask.title
  const taskOutput = inputs.task_output || ''

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['session', session.id] })
    queryClient.invalidateQueries({ queryKey: ['session-tasks', session.id] })
    queryClient.invalidateQueries({ queryKey: ['session-messages', session.id] })
  }

  const mutation = useMutation({
    mutationFn: (content: string) => sessionsApi.answerInput(session.id, activeTask.id, content),
    onSuccess: invalidate,
  })

  const confirm = () => {
    if (mutation.isPending) return
    mutation.mutate('用户已确认任务完成。')
  }

  const submitRejection = () => {
    if (mutation.isPending) return
    const msg = feedback.trim()
      ? `用户表示任务未完成，请重试。用户补充说明：${feedback.trim()}`
      : '用户表示任务未完成，请重试。'
    mutation.mutate(msg)
  }

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 100) + 'px'
  }, [feedback])

  return (
    <div className="border-t border-amber-200 bg-amber-50 p-3">
      <div className="flex items-start gap-2 mb-2">
        <MessageCircleQuestion size={15} className="text-amber-600 mt-0.5 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm text-amber-800 font-medium">
            Agent 未明确标记完成状态，请确认任务是否已完成
          </p>
          <p className="text-xs text-amber-700 mt-0.5 truncate">任务：{taskTitle}</p>
        </div>
      </div>

      {taskOutput && (
        <div className="mb-3 bg-white border border-amber-100 rounded-md px-3 py-2 max-h-28 overflow-y-auto">
          <pre className="text-xs text-gray-600 whitespace-pre-wrap break-words">{taskOutput}</pre>
        </div>
      )}

      {mutation.isError && (
        <p className="text-xs text-red-500 mb-2">提交失败，请重试</p>
      )}

      {!rejected ? (
        <div className="flex gap-2">
          <button
            onClick={confirm}
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
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="（可选）补充说明，帮助 Agent 重试… (Enter 提交，Shift+Enter 换行)"
            rows={1}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submitRejection()
              }
            }}
            className="resize-none rounded-lg border border-red-200 bg-white px-3 py-2 text-sm outline-none focus:border-red-400 focus:ring-1 focus:ring-red-300 leading-5"
          />
          <div className="flex gap-2">
            <button
              onClick={() => setRejected(false)}
              disabled={mutation.isPending}
              className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700 disabled:opacity-50"
            >
              返回
            </button>
            <button
              onClick={submitRejection}
              disabled={mutation.isPending}
              className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-red-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50 transition-colors"
            >
              {mutation.isPending ? <Spinner size="sm" /> : <><Send size={12} />提交，让 Agent 重试</>}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function UserInputPrompt({ session, tasks }: { session: Session; tasks: Task[] }) {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const queryClient = useQueryClient()

  const activeTask = tasks.find((t) => t.type === 'user_input' && t.status === 'ACTIVE')

  const mutation = useMutation({
    mutationFn: (content: string) =>
      sessionsApi.answerInput(session.id, activeTask!.id, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['session', session.id] })
      queryClient.invalidateQueries({ queryKey: ['session-tasks', session.id] })
      queryClient.invalidateQueries({ queryKey: ['session-messages', session.id] })
      setText('')
    },
  })

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }, [text])

  // 任务完成确认弹框（所有 hooks 已在上方声明，此处可安全 early return）
  if (activeTask && (activeTask.inputs as Record<string, string>).type === 'task_completion_confirm') {
    return <TaskCompletionConfirm session={session} activeTask={activeTask} />
  }

  const prompt = activeTask
    ? (activeTask.inputs as Record<string, string>).prompt || activeTask.description || activeTask.title
    : '请输入您的回复'

  const submit = () => {
    const trimmed = text.trim()
    if (!trimmed || mutation.isPending || !activeTask) return
    mutation.mutate(trimmed)
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="border-t border-amber-200 bg-amber-50 p-3">
      <div className="flex items-start gap-2 mb-2">
        <MessageCircleQuestion size={15} className="text-amber-600 mt-0.5 flex-shrink-0" />
        <p className="text-sm text-amber-800 font-medium">{prompt}</p>
      </div>
      {mutation.isError && (
        <p className="text-xs text-red-500 mb-2">提交失败，请重试</p>
      )}
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="输入您的回复… (Enter 提交，Shift+Enter 换行)"
          rows={1}
          className="flex-1 resize-none rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm outline-none focus:border-amber-500 focus:ring-1 focus:ring-amber-400 leading-5"
        />
        <button
          onClick={submit}
          disabled={!text.trim() || mutation.isPending || !activeTask}
          className={clsx(
            'flex items-center justify-center w-8 h-8 rounded-lg transition-colors flex-shrink-0',
            text.trim() && !mutation.isPending && activeTask
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

function SessionDetail({ sessionId }: { sessionId: string }) {
  const [tab, setTab] = useState<DetailTab>('tasks')

  const { data: session } = useSession(sessionId)
  const { data: tasks = [], isLoading: tasksLoading } = useSessionTasks(
    sessionId,
    session?.status
  )
  const { data: messages = [], isLoading: messagesLoading } = useSessionMessages(
    sessionId,
    session?.status
  )
  const { data: toolCalls = [], isLoading: toolCallsLoading } = useSessionToolCalls(
    sessionId,
    session?.status
  )

  if (!session) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    )
  }

  const tabs: { id: DetailTab; label: string; count?: number }[] = [
    { id: 'tasks', label: '任务流', count: tasks.length },
    { id: 'messages', label: '消息记录', count: messages.length },
    { id: 'toolcalls', label: '工具调用', count: toolCalls.length },
  ]

  return (
    <div className="flex flex-col h-full">
      {/* Status card */}
      <div className="p-4 border-b border-gray-100">
        <SessionStats session={session} />
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200 px-4">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={clsx(
              'px-3 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px',
              tab === t.id
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            {t.label}
            {t.count !== undefined && t.count > 0 && (
              <span
                className={clsx(
                  'ml-1.5 text-xs px-1.5 py-0.5 rounded-full',
                  tab === t.id ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-500'
                )}
              >
                {t.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-4">
        {tab === 'tasks' && (
          <TaskTimeline tasks={tasks} isLoading={tasksLoading} />
        )}
        {tab === 'messages' && (
          <MessageList messages={messages} isLoading={messagesLoading} />
        )}
        {tab === 'toolcalls' && (
          <ToolCallsTable toolCalls={toolCalls} isLoading={toolCallsLoading} />
        )}
      </div>

      {/* Bottom input area */}
      {session.status === 'WAITING_INPUT'
        ? <UserInputPrompt session={session} tasks={tasks} />
        : <ChatInput sessionId={sessionId} />
      }
    </div>
  )
}

export function SessionsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filter, setFilter] = useState<FilterStatus>('ALL')
  const [showCreate, setShowCreate] = useState(false)

  const { data: sessions = [], isLoading } = useQuery({
    queryKey: ['sessions'],
    queryFn: sessionsApi.list,
    refetchInterval: 3000,
  })

  const filtered = filter === 'ALL'
    ? sessions
    : sessions.filter((s) => s.status === filter)

  return (
    <div className="flex h-full">
      {/* Left panel */}
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
                onClick={() => setSelectedId(s.id)}
                onDeleted={() => { if (selectedId === s.id) setSelectedId(null) }}
              />
            ))
          )}
        </div>
      </div>

      {/* Right panel */}
      <div className="flex-1 overflow-hidden">
        {selectedId ? (
          <SessionDetail sessionId={selectedId} />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-3">
            <Inbox size={32} />
            <p className="text-sm">选择一个 Session 查看详情</p>
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
        onCreated={(id) => setSelectedId(id)}
      />
    </div>
  )
}
