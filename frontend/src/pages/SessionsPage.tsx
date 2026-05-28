import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Inbox, Send, Bot, FilePlus, X, ChevronRight, ChevronDown, FolderOpen, Folder } from 'lucide-react'
import { sessionsApi } from '@/api/sessions'
import type { SessionStatus, SessionConfig, Project } from '@/types'
import { NO_PROJECT_ID } from '@/types'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { SessionCard } from '@/components/session/SessionCard'
import { CreateSessionDialog } from '@/components/session/CreateSessionDialog'
import { ChatPanel } from '@/components/chat/ChatPanel'
import { useProjectGroups } from '@/hooks/useProjectGroups'
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
  text: string
  onTextChange: (text: string) => void
  onCreated: (sessionId: string) => void
  onCancel: () => void
}

function NewSessionPanel({ config, text, onTextChange, onCreated, onCancel }: NewSessionPanelProps) {
  const queryClient = useQueryClient()
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
            onChange={e => onTextChange(e.target.value)}
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

// ── ProjectGroupHeader ────────────────────────────────────────────────────────

interface ProjectGroupHeaderProps {
  project: Project
  collapsed: boolean
  onToggle: () => void
  onCreateInProject?: () => void
}

function ProjectGroupHeader({ project, collapsed, onToggle, onCreateInProject }: ProjectGroupHeaderProps) {
  const isNoProject = project.id === NO_PROJECT_ID
  const Icon = isNoProject ? Folder : FolderOpen
  return (
    <div
      className="group flex items-center gap-1.5 px-3 py-1.5 cursor-pointer border-b border-gray-100 bg-gray-50/60 hover:bg-gray-100/80 transition-colors sticky top-0 z-[1]"
      onClick={onToggle}
    >
      <button className="text-gray-400">
        {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
      </button>
      <Icon size={12} className={isNoProject ? 'text-gray-400' : 'text-blue-400'} />
      <span
        className="text-xs font-medium text-gray-700 truncate flex-1"
        title={project.working_dir || '未指定工作目录的会话'}
      >
        {project.display_name}
      </span>
      <span className="text-[10px] text-gray-400 font-mono">{project.session_count}</span>
      {onCreateInProject && !isNoProject && (
        <button
          onClick={e => { e.stopPropagation(); onCreateInProject() }}
          className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-gray-400 hover:text-blue-600 hover:bg-blue-50 transition-all"
          title={`在 ${project.display_name} 项目内新建会话`}
        >
          <Plus size={12} />
        </button>
      )}
    </div>
  )
}

// ── DraftSessionCard ──────────────────────────────────────────────────────────

interface DraftSessionCardProps {
  draftText: string
  selected: boolean
  onClick: () => void
  onCancel: () => void
}

function DraftSessionCard({ draftText, selected, onClick, onCancel }: DraftSessionCardProps) {
  const handleCancel = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (draftText.trim() && !window.confirm('放弃这个未发送的新会话？')) return
    onCancel()
  }

  return (
    <div
      className={clsx(
        'px-4 py-3 cursor-pointer border-b border-gray-100 hover:bg-gray-50 transition-colors group',
        selected ? 'bg-blue-50 border-l-2 border-l-blue-500' : 'border-l-2 border-l-transparent'
      )}
      onClick={onClick}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5 flex-1 min-w-0">
          <FilePlus size={13} className="text-gray-400 flex-shrink-0" />
          <p className="text-sm text-gray-700 line-clamp-1 leading-snug">
            {draftText.trim() || '新建会话（草稿）'}
          </p>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 uppercase tracking-wide">
            草稿
          </span>
          <button
            onClick={handleCancel}
            className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 transition-all"
            title="放弃草稿"
          >
            <X size={13} />
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
  const [draftText, setDraftText] = useState('')
  const [filter, setFilter] = useState<FilterStatus>('ALL')
  const [showCreate, setShowCreate] = useState(false)
  const [createInitialWd, setCreateInitialWd] = useState<string>('')
  // 折叠状态：未在集合内默认展开；"无项目" 默认折叠
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(() => new Set([NO_PROJECT_ID]))

  const { data: sessions = [], isLoading } = useQuery({
    queryKey: ['sessions'],
    queryFn: sessionsApi.list,
    refetchInterval: 5000,
  })

  const filtered = filter === 'ALL'
    ? sessions
    : sessions.filter((s) => s.status === filter)

  const projects = useProjectGroups(filtered)

  const draftSelected = !selectedId && !!pendingConfig

  function handleConfigured(config: SessionConfig) {
    setPendingConfig(config)
    setSelectedId(null)
  }

  function handleSessionCreated(sessionId: string) {
    setPendingConfig(null)
    setDraftText('')
    setSelectedId(sessionId)
  }

  function handleDraftCancel() {
    setPendingConfig(null)
    setDraftText('')
  }

  function openCreate(initialWd: string = '') {
    setCreateInitialWd(initialWd)
    setShowCreate(true)
  }

  function toggleProject(id: string) {
    setCollapsedProjects(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className="flex h-full">
      {/* Left panel — session list */}
      <div className="w-72 flex-shrink-0 border-r border-gray-200 bg-white flex flex-col">
        {/* Header */}
        <div className="px-4 py-3 border-b border-gray-100">
          <div className="flex items-center justify-between mb-2.5">
            <h1 className="text-sm font-semibold text-gray-900">Sessions</h1>
            <Button size="sm" onClick={() => openCreate('')}>
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
          {pendingConfig && (
            <DraftSessionCard
              draftText={draftText}
              selected={draftSelected}
              onClick={() => setSelectedId(null)}
              onCancel={handleDraftCancel}
            />
          )}
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : filtered.length === 0 && !pendingConfig ? (
            <div className="flex flex-col items-center gap-2 py-12 text-gray-400">
              <Inbox size={24} />
              <p className="text-sm">暂无 Session</p>
              <Button size="sm" variant="secondary" onClick={() => openCreate('')}>
                创建第一个
              </Button>
            </div>
          ) : (
            projects.map((project) => {
              const collapsed = collapsedProjects.has(project.id)
              return (
                <div key={project.id}>
                  <ProjectGroupHeader
                    project={project}
                    collapsed={collapsed}
                    onToggle={() => toggleProject(project.id)}
                    onCreateInProject={() => openCreate(project.working_dir)}
                  />
                  {!collapsed && project.sessions.map((s) => (
                    <SessionCard
                      key={s.id}
                      session={s}
                      selected={selectedId === s.id}
                      onClick={() => setSelectedId(s.id)}
                      onDeleted={() => { if (selectedId === s.id) setSelectedId(null) }}
                      showProjectBadge={false}
                    />
                  ))}
                </div>
              )
            })
          )}
        </div>
      </div>

      {/* Right panel — chat */}
      <div className="flex-1 overflow-hidden bg-gray-50">
        {selectedId ? (
          <ChatPanel key={selectedId} sessionId={selectedId} />
        ) : pendingConfig ? (
          <NewSessionPanel
            config={pendingConfig}
            text={draftText}
            onTextChange={setDraftText}
            onCreated={handleSessionCreated}
            onCancel={handleDraftCancel}
          />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-3">
            <Inbox size={32} />
            <p className="text-sm">选择一个 Session 查看对话</p>
            <Button variant="secondary" onClick={() => openCreate('')}>
              <Plus size={14} />
              新建 Session
            </Button>
          </div>
        )}
      </div>

      <CreateSessionDialog
        open={showCreate}
        initialWorkingDir={createInitialWd}
        recentSessions={sessions}
        onClose={() => setShowCreate(false)}
        onConfigured={handleConfigured}
      />
    </div>
  )
}
