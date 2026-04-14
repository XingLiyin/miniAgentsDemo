import { useMutation, useQueryClient } from '@tanstack/react-query'
import { XCircle, AlertTriangle } from 'lucide-react'
import type { Session } from '@/types'
import { sessionsApi } from '@/api/sessions'
import { SessionStatusBadge } from './StatusBadge'
import { Progress } from '@/components/ui/progress'
import { Button } from '@/components/ui/button'
import { isTerminalSession } from '@/lib/status'

interface Props {
  session: Session
}

export function SessionStats({ session }: Props) {
  const queryClient = useQueryClient()
  const tokenPct = session.token_budget > 0
    ? (session.token_used / session.token_budget) * 100
    : 0

  const cancelMutation = useMutation({
    mutationFn: () => sessionsApi.cancel(session.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['session', session.id] })
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
    },
  })

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      {/* Top row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs text-gray-400">{session.id}</span>
            <SessionStatusBadge status={session.status} />
            {session.failure_counter > 0 && (
              <span className="inline-flex items-center gap-1 text-xs text-yellow-600">
                <AlertTriangle size={11} />
                {session.failure_counter} 失败
              </span>
            )}
          </div>
          <p className="text-sm text-gray-800 mt-1.5 leading-snug line-clamp-3">
            {session.user_prompt}
          </p>
        </div>
        {!isTerminalSession(session.status) && (
          <Button
            size="sm"
            variant="outline"
            className="flex-shrink-0 text-red-600 border-red-200 hover:bg-red-50"
            loading={cancelMutation.isPending}
            onClick={() => cancelMutation.mutate()}
          >
            <XCircle size={13} />
            取消
          </Button>
        )}
      </div>

      {/* Stats */}
      <div className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-gray-500">Token 用量</span>
            {tokenPct >= 80 && (
              <span className={`text-xs font-medium ${tokenPct >= 95 ? 'text-red-600' : 'text-yellow-600'}`}>
                {tokenPct.toFixed(0)}%
              </span>
            )}
          </div>
          <Progress value={session.token_used} max={session.token_budget} showLabel />
        </div>
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-gray-500">轮次</span>
            <span className="text-xs text-gray-400 tabular-nums">
              {/* turns from agent loop_guard, approximated */}
              max {session.root_max_turns}
            </span>
          </div>
          <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div className="h-full bg-purple-400 rounded-full" style={{ width: '0%' }} />
          </div>
        </div>
      </div>
    </div>
  )
}
