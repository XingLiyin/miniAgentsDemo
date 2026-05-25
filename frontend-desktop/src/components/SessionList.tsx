import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2Icon, FolderIcon, Wand2Icon, ZapIcon } from 'lucide-react'
import { sessionsApi } from '@/api/sessions'
import type { Session, PendingSession } from '@/types'
import { StatusBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatTime } from '@/lib/utils'
import { NewSessionDialog } from './NewSessionDialog'
import type { CenterView } from '@/App'

interface Props {
  selectedId: string | null
  pendingSession: PendingSession | null
  centerView: CenterView
  onViewChange: (view: CenterView) => void
  onSelect: (id: string) => void
  onNewSession: (pending: PendingSession) => void
  onPendingSelect: () => void
}

export function SessionList({ selectedId, pendingSession, centerView, onViewChange, onSelect, onNewSession, onPendingSelect }: Props) {
  const qc = useQueryClient()
  const [showNew, setShowNew] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const { data: sessions = [] } = useQuery({
    queryKey: ['sessions'],
    queryFn: sessionsApi.list,
    refetchInterval: 3000,
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => sessionsApi.delete(id),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      if (selectedId === id) onSelect('')
    },
  })

  function handleNewSession(pending: PendingSession) {
    setShowNew(false)
    onNewSession(pending)
  }

  return (
    <>
      <div className="flex h-full flex-col">
        {/* Header */}
        <div className="flex items-center gap-2.5 px-3 py-2.5" style={{ borderBottom: '1px solid var(--border)' }}>
          <img src="/icon.svg" alt="logo" style={{ width: 44, height: 44, flexShrink: 0 }} />
          <div className="flex flex-col leading-tight">
            <span style={{
              fontSize: 18, fontWeight: 700, letterSpacing: '-0.2px',
              background: 'linear-gradient(90deg, #2563eb, #0891b2)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}>CoWork</span>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--t2)', letterSpacing: '0.2px' }}>NetLIVE</span>
          </div>
        </div>

        {/* Nav: Skill 市场 / LLM 配置 */}
        <div className="py-1" style={{ borderBottom: '1px solid var(--border)' }}>
          <NavItem
            icon={<ZapIcon size={14} />}
            label="Skill 市场"
            active={centerView === 'skills'}
            onClick={() => onViewChange(centerView === 'skills' ? 'chat' : 'skills')}
          />
          <NavItem
            icon={<Wand2Icon size={14} />}
            label="LLM 配置"
            active={centerView === 'llm'}
            onClick={() => onViewChange(centerView === 'llm' ? 'chat' : 'llm')}
          />
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto py-1">
          {/* Session list title */}
          <div className="flex items-center justify-between px-3 py-1.5">
            <span className="text-xs font-semibold" style={{ color: 'var(--t3)', letterSpacing: '1px', textTransform: 'uppercase' }}>会话</span>
            <button
              onClick={() => setShowNew(true)}
              title="新建会话"
              style={{
                width: 20, height: 20, borderRadius: '50%', border: 'none',
                background: 'var(--blue-dim)', color: 'var(--blue)',
                fontSize: 16, lineHeight: 1, display: 'grid', placeItems: 'center',
                cursor: 'pointer', transition: 'var(--tr)',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--blue)'; (e.currentTarget as HTMLElement).style.color = '#fff' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'var(--blue-dim)'; (e.currentTarget as HTMLElement).style.color = 'var(--blue)' }}
            >＋</button>
          </div>
          {pendingSession && (
            <PendingSessionItem
              pending={pendingSession}
              selected={selectedId === null && centerView === 'chat'}
              onClick={onPendingSelect}
            />
          )}

          {sessions.length === 0 && !pendingSession && (
            <p className="px-3 py-4 text-center text-xs" style={{ color: 'var(--t3)' }}>暂无会话，点击 + 新建</p>
          )}

          {sessions.map(s => (
            <SessionItem
              key={s.id}
              session={s}
              selected={s.id === selectedId && centerView === 'chat'}
              onSelect={() => onSelect(s.id)}
              onDelete={() => setConfirmDelete(s.id)}
            />
          ))}
        </div>
      </div>

      {/* Delete confirm */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(15,31,61,.35)', backdropFilter: 'blur(4px)' }}>
          <div className="w-72 p-4" style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 12, boxShadow: '0 24px 80px rgba(15,31,61,.18)' }}>
            <p className="mb-4 text-sm" style={{ color: 'var(--t2)' }}>确认删除这个会话？此操作不可撤销。</p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirmDelete(null)}>取消</Button>
              <Button variant="danger" size="sm" onClick={() => { deleteMut.mutate(confirmDelete); setConfirmDelete(null) }}>删除</Button>
            </div>
          </div>
        </div>
      )}

      <NewSessionDialog open={showNew} onClose={() => setShowNew(false)} onCreated={handleNewSession} />
    </>
  )
}

function NavItem({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', width: '100%', alignItems: 'center', gap: 8,
        padding: '7px 12px', fontSize: 13, fontWeight: active ? 600 : 400,
        color: active ? 'var(--blue)' : 'var(--t2)',
        background: active ? 'var(--blue-dim)' : 'transparent',
        border: 'none', cursor: 'pointer', transition: 'var(--tr)',
      }}
      onMouseEnter={e => { if (!active) { (e.currentTarget as HTMLElement).style.background = 'var(--bg3)'; (e.currentTarget as HTMLElement).style.color = 'var(--t1)' } }}
      onMouseLeave={e => { if (!active) { (e.currentTarget as HTMLElement).style.background = 'transparent'; (e.currentTarget as HTMLElement).style.color = 'var(--t2)' } }}
    >
      {icon}
      {label}
    </button>
  )
}

function PendingSessionItem({ pending, selected, onClick }: { pending: PendingSession; selected: boolean; onClick: () => void }) {
  const dirName = pending.workingDir.split(/[\\/]/).filter(Boolean).pop() ?? pending.workingDir
  return (
    <div
      onClick={onClick}
      style={{
        cursor: 'pointer', padding: '6px 12px', transition: 'var(--tr)',
        background: selected ? 'var(--blue-dim)' : undefined,
        borderRadius: 'var(--r)', margin: '0 4px 2px',
      }}
      onMouseEnter={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = 'var(--bg3)' }}
      onMouseLeave={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = '' }}
    >
      <div className="flex items-center gap-1.5">
        <FolderIcon size={12} className="flex-shrink-0 text-yellow-500" />
        <p className="min-w-0 flex-1 truncate text-sm" style={{ color: 'var(--t1)' }}>{dirName}</p>
      </div>
      <p className="mt-0.5 text-[10px]" style={{ color: 'var(--t3)' }}>等待第一条消息…</p>
    </div>
  )
}

function SessionItem({ session, selected, onSelect, onDelete }: { session: Session; selected: boolean; onSelect: () => void; onDelete: () => void }) {
  const title = session.goal || session.user_prompt || session.id.slice(0, 8)
  return (
    <div
      onClick={onSelect}
      className="group relative cursor-pointer"
      style={{
        padding: '7px 9px', margin: '0 4px 2px', borderRadius: 'var(--r)',
        background: selected ? 'var(--bg3)' : undefined,
        border: selected ? '1px solid var(--border2)' : '1px solid transparent',
        transition: 'var(--tr)',
      }}
      onMouseEnter={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = 'var(--bg3)' }}
      onMouseLeave={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = '' }}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 flex-1 truncate text-sm" style={{ color: 'var(--t1)', fontWeight: 500 }}>{title}</p>
        <button
          onClick={e => { e.stopPropagation(); onDelete() }}
          className="invisible flex-shrink-0 group-hover:visible"
          style={{ color: 'var(--t3)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'grid', placeItems: 'center' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--red)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--t3)' }}
        >
          <Trash2Icon size={12} />
        </button>
      </div>
      <div className="mt-1 flex items-center gap-2">
        <StatusBadge status={session.status} />
        <span className="text-[10px]" style={{ color: 'var(--t3)' }}>{formatTime(session.created_at)}</span>
      </div>
    </div>
  )
}
