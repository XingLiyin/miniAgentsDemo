import { clsx } from 'clsx'
import type { Session } from '@/types'
import { SessionStatusBadge } from './StatusBadge'
import { formatRelativeTime } from '@/lib/status'

interface SessionCardProps {
  session: Session
  selected: boolean
  onClick: () => void
}

export function SessionCard({ session, selected, onClick }: SessionCardProps) {
  const tokenPct = session.token_budget > 0
    ? Math.min((session.token_used / session.token_budget) * 100, 100)
    : 0

  return (
    <div
      className={clsx(
        'px-4 py-3 cursor-pointer border-b border-gray-100 hover:bg-gray-50 transition-colors',
        selected ? 'bg-blue-50 border-l-2 border-l-blue-500' : 'border-l-2 border-l-transparent'
      )}
      onClick={onClick}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-gray-900 line-clamp-2 flex-1 leading-snug">
          {session.goal}
        </p>
        <SessionStatusBadge status={session.status} />
      </div>
      <div className="flex items-center gap-2 mt-1.5">
        <span className="text-xs text-gray-400 font-mono">
          {session.id.slice(0, 14)}...
        </span>
        <span className="text-xs text-gray-300">·</span>
        <span className="text-xs text-gray-400">
          {formatRelativeTime(session.created_at)}
        </span>
      </div>
      {tokenPct > 0 && (
        <div className="mt-2 h-0.5 bg-gray-200 rounded-full overflow-hidden">
          <div
            className={clsx(
              'h-full rounded-full transition-all',
              tokenPct >= 95 ? 'bg-red-400' : tokenPct >= 80 ? 'bg-yellow-400' : 'bg-blue-400'
            )}
            style={{ width: `${tokenPct}%` }}
          />
        </div>
      )}
    </div>
  )
}
