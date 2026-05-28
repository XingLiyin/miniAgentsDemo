import React, { useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2Icon, FolderIcon, FolderOpenIcon, Wand2Icon, ZapIcon, ChevronRightIcon, ChevronDownIcon, PlusIcon, XIcon, SettingsIcon, GlobeIcon } from 'lucide-react'
import { sessionsApi } from '@/api/sessions'
import type { Session, PendingSession } from '@/types'
import { StatusBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatTime } from '@/lib/utils'
import { NewSessionDialog } from './NewSessionDialog'
import { useProjectGroups, NO_PROJECT_ID, type Project } from '@/hooks/useProjectGroups'
import { useI18n, LANGUAGES, type Lang } from '@/i18n'
import type { CenterView } from '@/App'

interface Props {
  selectedId: string | null
  pendingSession: PendingSession | null
  centerView: CenterView
  onViewChange: (view: CenterView) => void
  onSelect: (id: string) => void
  onNewSession: (pending: PendingSession) => void
  onPendingSelect: () => void
  onDismissDraft: () => void
}

export function SessionList({ selectedId, pendingSession, centerView, onViewChange, onSelect, onNewSession, onPendingSelect, onDismissDraft }: Props) {
  const qc = useQueryClient()
  const { t, lang, setLang } = useI18n()
  const [showNew, setShowNew] = useState(false)
  const [createInitialWd, setCreateInitialWd] = useState<string>('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  // 折叠状态：默认全展开；"未指定目录" 默认折叠
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(() => new Set([NO_PROJECT_ID]))
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [version, setVersion] = useState('')
  const settingsBtnRef = useRef<HTMLButtonElement>(null)

  // 取应用版本号（Electron 下）
  useEffect(() => {
    window.electronAPI?.getVersion?.().then(setVersion).catch(() => {})
  }, [])

  // 点设置区外面自动收起
  useEffect(() => {
    if (!settingsOpen) return
    function onClickOutside(e: MouseEvent) {
      const btn = settingsBtnRef.current
      if (!btn) return
      const target = e.target as Node
      // 点击按钮自身不关闭（让按钮自己 toggle）
      if (btn.contains(target)) return
      // 点击弹出菜单内部不关闭
      const menu = document.getElementById('settings-popup-menu')
      if (menu && menu.contains(target)) return
      setSettingsOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [settingsOpen])

  const { data: sessions = [] } = useQuery({
    queryKey: ['sessions'],
    queryFn: sessionsApi.list,
    refetchInterval: 3000,
  })

  const projects = useProjectGroups(sessions)

  const deleteMut = useMutation({
    mutationFn: (id: string) => sessionsApi.delete(id),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      if (selectedId === id) onSelect('')
    },
  })

  function handleNewSession(pending: PendingSession) {
    setShowNew(false)
    setCreateInitialWd('')
    onNewSession(pending)
  }

  function openCreate(initialWd: string = '') {
    setCreateInitialWd(initialWd)
    setShowNew(true)
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
    <>
      <div className="flex h-full flex-col">
        {/* Session list */}
        <div className="flex-1 overflow-y-auto py-1">
          {/* Session list title */}
          <div className="flex items-center justify-between px-3 py-1.5">
            <span className="text-xs font-semibold" style={{ color: 'var(--t3)', letterSpacing: '1px', textTransform: 'uppercase' }}>{t('sidebar.sessions')}</span>
            <button
              onClick={() => openCreate('')}
              title={t('sidebar.newSession')}
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
              onDismiss={onDismissDraft}
            />
          )}

          {sessions.length === 0 && !pendingSession && (
            <p className="px-3 py-4 text-center text-xs" style={{ color: 'var(--t3)' }}>{t('sidebar.noSessions')}</p>
          )}

          {projects.map(project => {
            const collapsed = collapsedProjects.has(project.id)
            return (
              <div key={project.id}>
                <ProjectGroupHeader
                  project={project}
                  collapsed={collapsed}
                  onToggle={() => toggleProject(project.id)}
                  onCreateInProject={() => openCreate(project.working_dir)}
                />
                {!collapsed && project.sessions.map(s => (
                  <SessionItem
                    key={s.id}
                    session={s}
                    selected={s.id === selectedId && centerView === 'chat'}
                    onSelect={() => onSelect(s.id)}
                    onDelete={() => setConfirmDelete(s.id)}
                  />
                ))}
              </div>
            )
          })}
        </div>

        {/* Bottom: 设置 —— 无上边线，靠侧边栏 bg2 整体色块自身包裹感分隔 */}
        <div className="relative" style={{ padding: '4px' }}>
          {settingsOpen && (
            <div
              id="settings-popup-menu"
              style={{
                position: 'absolute', bottom: 'calc(100% + 2px)', left: 4, right: 4,
                background: 'var(--bg1)', border: '1px solid var(--border)',
                borderRadius: 'var(--r)', boxShadow: '0 8px 24px rgba(15,31,61,.12)',
                overflow: 'hidden', zIndex: 20,
              }}
            >
              <NavItem
                icon={<ZapIcon size={14} />}
                label={t('sidebar.skillMarket')}
                active={centerView === 'skills'}
                onClick={() => {
                  onViewChange(centerView === 'skills' ? 'chat' : 'skills')
                  setSettingsOpen(false)
                }}
              />
              <NavItem
                icon={<Wand2Icon size={14} />}
                label={t('sidebar.llmConfig')}
                active={centerView === 'llm'}
                onClick={() => {
                  onViewChange(centerView === 'llm' ? 'chat' : 'llm')
                  setSettingsOpen(false)
                }}
              />

              {/* 语言切换 */}
              <div style={{ borderTop: '1px solid var(--border)' }} />
              <div className="flex items-center justify-between px-3 py-2">
                <div className="flex items-center gap-2">
                  <GlobeIcon size={14} style={{ color: 'var(--t3)' }} />
                  <span style={{ fontSize: 13, color: 'var(--t2)' }}>{t('settings.language')}</span>
                </div>
                <div className="flex items-center gap-0.5" style={{ background: 'var(--bg3)', borderRadius: 6, padding: 2 }}>
                  {LANGUAGES.map(opt => (
                    <button
                      key={opt.value}
                      onClick={() => setLang(opt.value as Lang)}
                      style={{
                        fontSize: 11, padding: '2px 8px', borderRadius: 4, border: 'none', cursor: 'pointer',
                        background: lang === opt.value ? 'var(--bg1)' : 'transparent',
                        color: lang === opt.value ? 'var(--blue)' : 'var(--t3)',
                        fontWeight: lang === opt.value ? 600 : 400,
                        boxShadow: lang === opt.value ? '0 1px 2px rgba(15,31,61,.1)' : 'none',
                        transition: 'var(--tr)',
                      }}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* 版本号 */}
              <div className="flex items-center justify-between px-3 pb-2" style={{ fontSize: 11, color: 'var(--t3)' }}>
                <span>{t('settings.version')}</span>
                <span style={{ fontFamily: 'monospace' }}>{version ? `V${version}` : '—'}</span>
              </div>
            </div>
          )}
          <button
            ref={settingsBtnRef}
            onClick={() => setSettingsOpen(v => !v)}
            style={{
              display: 'flex', width: '100%', alignItems: 'center', gap: 8,
              padding: '8px 10px', fontSize: 13, fontWeight: settingsOpen ? 600 : 500,
              color: settingsOpen ? 'var(--blue)' : 'var(--t2)',
              background: settingsOpen ? 'var(--blue-dim)' : 'transparent',
              border: 'none', cursor: 'pointer', borderRadius: 'var(--r)',
              transition: 'var(--tr)',
            }}
            onMouseEnter={e => { if (!settingsOpen) { (e.currentTarget as HTMLElement).style.background = 'var(--bg3)'; (e.currentTarget as HTMLElement).style.color = 'var(--t1)' } }}
            onMouseLeave={e => { if (!settingsOpen) { (e.currentTarget as HTMLElement).style.background = 'transparent'; (e.currentTarget as HTMLElement).style.color = 'var(--t2)' } }}
          >
            <SettingsIcon size={14} />
            {t('sidebar.settings')}
          </button>
        </div>
      </div>

      {/* Delete confirm */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(15,31,61,.35)', backdropFilter: 'blur(4px)' }}>
          <div className="w-72 p-4" style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 12, boxShadow: '0 24px 80px rgba(15,31,61,.18)' }}>
            <p className="mb-4 text-sm" style={{ color: 'var(--t2)' }}>{t('sidebar.deleteSessionConfirm')}</p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirmDelete(null)}>{t('common.cancel')}</Button>
              <Button variant="danger" size="sm" onClick={() => { deleteMut.mutate(confirmDelete); setConfirmDelete(null) }}>{t('common.delete')}</Button>
            </div>
          </div>
        </div>
      )}

      <NewSessionDialog
        open={showNew}
        initialWorkingDir={createInitialWd}
        recentSessions={sessions}
        onClose={() => { setShowNew(false); setCreateInitialWd('') }}
        onCreated={handleNewSession}
      />
    </>
  )
}

// ── ProjectGroupHeader ────────────────────────────────────────────────────────

function ProjectGroupHeader({ project, collapsed, onToggle, onCreateInProject }: {
  project: Project; collapsed: boolean; onToggle: () => void; onCreateInProject: () => void
}) {
  const { t } = useI18n()
  const isNoProject = project.id === NO_PROJECT_ID
  const Icon = isNoProject ? FolderIcon : FolderOpenIcon
  const displayName = isNoProject ? t('sidebar.noProject') : project.display_name
  return (
    <div
      className="group flex cursor-pointer items-center gap-1"
      onClick={onToggle}
      style={{
        padding: '5px 9px', margin: '0 4px 1px',
        transition: 'var(--tr)',
      }}
      title={project.working_dir || t('sidebar.noProject')}
      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--bg3)' }}
      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = '' }}
    >
      {collapsed ? <ChevronRightIcon size={11} style={{ color: 'var(--t3)' }} /> : <ChevronDownIcon size={11} style={{ color: 'var(--t3)' }} />}
      <Icon size={11} style={{ color: isNoProject ? 'var(--t3)' : '#eab308' }} />
      <span className="min-w-0 flex-1 truncate" style={{ fontSize: 12, fontWeight: 500, color: 'var(--t2)' }}>
        {displayName}
      </span>
      <span style={{ fontSize: 10, color: 'var(--t3)', fontFamily: 'monospace' }}>{project.session_count}</span>
      {!isNoProject && (
        <button
          onClick={e => { e.stopPropagation(); onCreateInProject() }}
          className="invisible group-hover:visible"
          title={t('sidebar.createInProject', { name: project.display_name })}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: 'var(--t3)', padding: 0, display: 'grid', placeItems: 'center',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--blue)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--t3)' }}
        >
          <PlusIcon size={11} />
        </button>
      )}
    </div>
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

function PendingSessionItem({ pending, selected, onClick, onDismiss }: { pending: PendingSession; selected: boolean; onClick: () => void; onDismiss: () => void }) {
  const { t } = useI18n()
  const dirName = pending.workingDir.split(/[\\/]/).filter(Boolean).pop() ?? pending.workingDir
  return (
    <div
      onClick={onClick}
      className="group"
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
        <button
          onClick={e => { e.stopPropagation(); onDismiss() }}
          className="invisible flex-shrink-0 group-hover:visible"
          title={t('sidebar.dismissDraft')}
          style={{ color: 'var(--t3)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'grid', placeItems: 'center' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--red)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--t3)' }}
        >
          <XIcon size={11} />
        </button>
      </div>
      <p className="mt-0.5 text-[10px]" style={{ color: 'var(--t3)' }}>{t('sidebar.draftWaiting')}</p>
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
