import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Inbox } from 'lucide-react'
import { sessionsApi } from '@/api/sessions'
import type { SessionStatus } from '@/types'
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

export function SessionsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
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
                onClick={() => setSelectedId(s.id)}
                onDeleted={() => { if (selectedId === s.id) setSelectedId(null) }}
              />
            ))
          )}
        </div>
      </div>

      {/* Right panel — chat */}
      <div className="flex-1 overflow-hidden bg-gray-50">
        {selectedId ? (
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
        onCreated={(id) => setSelectedId(id)}
      />
    </div>
  )
}
