import { cn } from '@/lib/utils'
import type { SessionStatus } from '@/types'

const STATUS_STYLES: Record<SessionStatus, string> = {
  QUEUED:        'bg-[#eaf0fb] text-[#8aa3bf]',
  RUNNING:       'bg-[rgba(37,99,235,0.09)] text-[#2563eb] animate-pulse',
  WAITING_INPUT: 'bg-amber-50 text-amber-600 animate-pulse',
  PAUSED_HITL:   'bg-amber-50 text-amber-600',
  SUCCEEDED:     'bg-emerald-50 text-emerald-600',
  FAILED:        'bg-red-50 text-red-600',
  CANCELED:      'bg-[#eaf0fb] text-[#8aa3bf]',
  INTERRUPTED:   'bg-[#eaf0fb] text-[#8aa3bf]',
}

const STATUS_LABELS: Record<SessionStatus, string> = {
  QUEUED:        '等待中',
  RUNNING:       '运行中',
  WAITING_INPUT: '等待输入',
  PAUSED_HITL:   '暂停',
  SUCCEEDED:     '完成',
  FAILED:        '失败',
  CANCELED:      '已取消',
  INTERRUPTED:   '已中断',
}

export function StatusBadge({ status, className }: { status: SessionStatus; className?: string }) {
  return (
    <span className={cn('inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium', STATUS_STYLES[status], className)}>
      {STATUS_LABELS[status]}
    </span>
  )
}
