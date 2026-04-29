import { useState } from 'react'
import { clsx } from 'clsx'
import { CheckCircle2, XCircle, Circle, Loader2, MinusCircle, ChevronDown, ChevronRight } from 'lucide-react'
import type { Task } from '@/types'
import { TaskStatusBadge } from '@/components/session/StatusBadge'
import { Spinner } from '@/components/ui/spinner'
import { formatRelativeTime } from '@/lib/status'

function TaskIcon({ status }: { status: Task['status'] }) {
  switch (status) {
    case 'FINISHED': return <CheckCircle2 size={16} className="text-green-500 flex-shrink-0" />
    case 'FAILED': return <XCircle size={16} className="text-red-500 flex-shrink-0" />
    case 'ACTIVE': return <Loader2 size={16} className="text-blue-500 flex-shrink-0 animate-spin" />
    case 'CANCELED': return <MinusCircle size={16} className="text-gray-400 flex-shrink-0" />
    default: return <Circle size={16} className="text-gray-300 flex-shrink-0" />
  }
}

function Collapsible({ label, children, defaultOpen = false, labelClass }: {
  label: string
  children: React.ReactNode
  defaultOpen?: boolean
  labelClass?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="mt-1.5">
      <button
        onClick={() => setOpen(v => !v)}
        className={clsx('flex items-center gap-1 text-xs transition-colors', labelClass ?? 'text-gray-400 hover:text-gray-600')}
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <span>{label}</span>
      </button>
      {open && <div className="mt-1">{children}</div>}
    </div>
  )
}

function TaskCard({ task }: { task: Task }) {
  const isActive = task.status === 'ACTIVE'

  return (
    <div
      className={clsx(
        'rounded-xl border p-3 transition-colors',
        isActive
          ? 'border-blue-200 bg-blue-50'
          : task.status === 'FAILED'
          ? 'border-red-100 bg-red-50'
          : 'border-gray-100 bg-white'
      )}
    >
      <div className="flex items-start gap-2">
        <TaskIcon status={task.status} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-gray-900">{task.title}</span>
            <TaskStatusBadge status={task.status} />
          </div>

          {task.description && (
            <Collapsible label="描述">
              <p className="text-xs text-gray-500 leading-relaxed">{task.description}</p>
            </Collapsible>
          )}

          {task.result && (
            <Collapsible label="结果" defaultOpen={!isActive} labelClass="text-green-600 hover:text-green-700">
              <pre className="text-xs text-gray-700 bg-gray-50 border border-gray-100 rounded-md px-3 py-2 overflow-x-auto whitespace-pre-wrap break-words max-h-48">
                {task.result}
              </pre>
            </Collapsible>
          )}

          {task.error && (
            <Collapsible label="错误" defaultOpen labelClass="text-red-500 hover:text-red-600">
              <pre className="text-xs text-red-700 bg-red-50 border border-red-100 rounded-md px-3 py-2 overflow-x-auto whitespace-pre-wrap">
                {task.error}
              </pre>
            </Collapsible>
          )}

          {isActive && (
            <div className="flex items-center gap-1.5 mt-2 text-xs text-blue-600">
              <Spinner size="sm" />
              <span>执行中...</span>
            </div>
          )}

          <p className="text-xs text-gray-400 mt-1.5">
            {formatRelativeTime(task.created_at)}
          </p>
        </div>
      </div>
    </div>
  )
}

interface TaskTimelineProps {
  tasks: Task[]
  isLoading: boolean
}

export function TaskTimeline({ tasks, isLoading }: TaskTimelineProps) {
  if (isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    )
  }

  if (tasks.length === 0) {
    return (
      <div className="text-center py-8 text-sm text-gray-400">
        暂无任务，Agent 尚未开始工作
      </div>
    )
  }

  // Show newest first with active tasks at top
  const sorted = [...tasks].sort((a, b) => {
    if (a.status === 'ACTIVE') return -1
    if (b.status === 'ACTIVE') return 1
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  })

  return (
    <div className="flex flex-col gap-2">
      {sorted.map((task) => (
        <TaskCard key={task.id} task={task} />
      ))}
    </div>
  )
}
