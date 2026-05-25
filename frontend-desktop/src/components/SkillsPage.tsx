import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2Icon, TagIcon, DownloadIcon, CheckCircle2Icon, SearchIcon, PackageIcon, ZapIcon } from 'lucide-react'
import { skillsApi } from '@/api/skills'
import type { LocalSkill, RemoteCatalogItem } from '@/api/skills'
import { Button } from '@/components/ui/button'

type Tab = 'local' | 'remote'

export function SkillsPage() {
  const [tab, setTab] = useState<Tab>('local')

  return (
    <div className="flex h-full flex-col" style={{ background: 'var(--bg0)' }}>
      {/* Header */}
      <div style={{ background: 'var(--bg1)', borderBottom: '1px solid var(--border)' }}>
        <div className="px-6 pt-5 pb-0">
          <div className="flex items-center gap-2 mb-4">
            <ZapIcon size={18} style={{ color: 'var(--blue)' }} />
            <h1 className="text-base font-semibold" style={{ color: 'var(--t1)' }}>Skills</h1>
          </div>
          {/* Tabs */}
          <div className="flex gap-0">
            <TabButton active={tab === 'local'} onClick={() => setTab('local')}>本地 Skills</TabButton>
            <TabButton active={tab === 'remote'} onClick={() => setTab('remote')}>Skill 市场</TabButton>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {tab === 'local' ? <LocalPanel /> : <RemotePanel />}
      </div>
    </div>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="relative px-1 pb-3 mr-6 text-sm font-medium transition-colors"
      style={{
        color: active ? 'var(--blue)' : 'var(--t3)',
        background: 'none',
        border: 'none',
        cursor: 'pointer',
        borderBottom: active ? '2px solid var(--blue)' : '2px solid transparent',
      }}
    >
      {children}
    </button>
  )
}

// ── 本地 Skills ────────────────────────────────────────────────────────────

function LocalPanel() {
  const qc = useQueryClient()
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const { data: skills = [], isLoading } = useQuery({
    queryKey: ['skills'],
    queryFn: skillsApi.list,
  })

  const deleteMut = useMutation({
    mutationFn: (skillId: string) => skillsApi.delete(skillId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['skills'] }); setConfirmId(null) },
  })

  return (
    <>
      <div className="p-5">
        {isLoading ? (
          <div className="flex flex-col gap-3">
            {[1, 2, 3].map(i => <SkeletonCard key={i} />)}
          </div>
        ) : skills.length === 0 ? (
          <EmptyState icon={<PackageIcon size={32} />} title="暂无本地 Skill" desc="前往 Skill 市场下载 Skills 到本地使用" />
        ) : (
          <div className="flex flex-col gap-2">
            {skills.map(s => (
              <SkillCard key={s.skill_id} skill={s} onDelete={() => setConfirmId(s.skill_id)} />
            ))}
          </div>
        )}
      </div>

      {confirmId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(15,31,61,.4)', backdropFilter: 'blur(4px)' }}>
          <div className="w-80 p-5" style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 16, boxShadow: '0 24px 80px rgba(15,31,61,.2)' }}>
            <div className="flex items-center gap-2 mb-1">
              <Trash2Icon size={15} style={{ color: 'var(--red)' }} />
              <p className="text-sm font-semibold" style={{ color: 'var(--t1)' }}>删除 Skill</p>
            </div>
            <p className="mt-2 mb-5 text-xs leading-relaxed" style={{ color: 'var(--t2)' }}>
              确认删除 <span className="font-mono font-semibold" style={{ color: 'var(--t1)' }}>{confirmId}</span>？<br />
              将同时删除对应目录，此操作不可撤销。
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirmId(null)}>取消</Button>
              <Button variant="danger" size="sm" loading={deleteMut.isPending}
                onClick={() => deleteMut.mutate(confirmId)}>删除</Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function SkillCard({ skill, onDelete }: { skill: LocalSkill; onDelete: () => void }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div
      className="rounded-xl transition-shadow"
      style={{ border: '1px solid var(--border)', background: 'var(--bg1)', overflow: 'hidden', boxShadow: 'var(--shadow)' }}
    >
      <div
        className="flex items-center justify-between gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setExpanded(e => !e)}
        style={{ userSelect: 'none' }}
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'var(--blue-dim)' }}>
            <ZapIcon size={13} style={{ color: 'var(--blue)' }} />
          </div>
          <p className="truncate text-sm font-medium" style={{ color: 'var(--t1)' }}>{skill.name}</p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="rounded-full px-2 py-0.5 text-[10px] font-mono" style={{ background: 'var(--bg3)', color: 'var(--t2)' }}>
            v{skill.version}
          </span>
          <span style={{ color: 'var(--t3)', fontSize: 11 }}>{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-4" style={{ borderTop: '1px solid var(--border)' }}>
          {skill.description && (
            <p className="mt-3 text-xs leading-relaxed" style={{ color: 'var(--t2)' }}>{skill.description}</p>
          )}
          {skill.triggers.length > 0 && (
            <div className="mt-2.5 flex flex-wrap gap-1">
              {skill.triggers.map(t => (
                <span key={t} className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium" style={{ background: 'var(--blue-dim)', color: 'var(--blue)' }}>
                  <TagIcon size={9} />{t}
                </span>
              ))}
            </div>
          )}
          <div className="mt-3 flex justify-end">
            <button
              onClick={e => { e.stopPropagation(); onDelete() }}
              className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg transition-colors"
              style={{ color: 'var(--t3)', background: 'none', border: '1px solid transparent', cursor: 'pointer' }}
              onMouseEnter={e => {
                const el = e.currentTarget as HTMLElement
                el.style.color = 'var(--red)'
                el.style.background = 'rgba(220,38,38,.06)'
                el.style.borderColor = 'rgba(220,38,38,.2)'
              }}
              onMouseLeave={e => {
                const el = e.currentTarget as HTMLElement
                el.style.color = 'var(--t3)'
                el.style.background = 'none'
                el.style.borderColor = 'transparent'
              }}
            >
              <Trash2Icon size={12} />删除
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Skill 市场 ─────────────────────────────────────────────────────────────

function RemotePanel() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')

  const { data: catalog = [], isLoading, isError, error } = useQuery({
    queryKey: ['skill-catalog'],
    queryFn: skillsApi.catalog,
    retry: 1,
  })

  const pullMut = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => skillsApi.pull(id, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] })
      qc.invalidateQueries({ queryKey: ['skill-catalog'] })
    },
  })

  const filtered = useMemo(() => {
    if (!search.trim()) return catalog
    const q = search.toLowerCase()
    return catalog.filter(item =>
      item.name.toLowerCase().includes(q) ||
      item.description?.toLowerCase().includes(q) ||
      item.domain?.toLowerCase().includes(q)
    )
  }, [catalog, search])

  return (
    <div className="p-5">
      {/* Search bar */}
      {!isLoading && !isError && catalog.length > 0 && (
        <div className="relative mb-4">
          <SearchIcon size={14} className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: 'var(--t3)' }} />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="搜索 Skill 名称、描述或分类…"
            className="w-full rounded-xl pl-9 pr-3 py-2.5 text-sm outline-none transition-colors"
            style={{
              background: 'var(--bg1)',
              border: '1px solid var(--border)',
              color: 'var(--t1)',
            }}
            onFocus={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--blue)' }}
            onBlur={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)' }}
          />
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-2 gap-3">
          {[1, 2, 3, 4].map(i => <SkeletonCard key={i} tall />)}
        </div>
      ) : isError ? (
        <EmptyState
          icon={<span style={{ fontSize: 32 }}>⚠️</span>}
          title="获取失败"
          desc={(error as Error)?.message ?? '无法连接到 Skill 服务器，请检查网络连接'}
          variant="error"
        />
      ) : catalog.length === 0 ? (
        <EmptyState icon={<PackageIcon size={32} />} title="远端暂无可用 Skill" desc="稍后再来查看" />
      ) : filtered.length === 0 ? (
        <EmptyState icon={<SearchIcon size={32} />} title="未找到匹配的 Skill" desc={`没有与 "${search}" 相关的结果`} />
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {filtered.map(item => (
            <CatalogCard
              key={item.id}
              item={item}
              pulling={pullMut.isPending && pullMut.variables?.id === item.id}
              onPull={() => pullMut.mutate({ id: item.id, name: item.name })}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function CatalogCard({ item, pulling, onPull }: { item: RemoteCatalogItem; pulling: boolean; onPull: () => void }) {
  return (
    <div
      className="rounded-xl flex flex-col"
      style={{
        border: '1px solid var(--border)',
        background: 'var(--bg1)',
        boxShadow: 'var(--shadow)',
        overflow: 'hidden',
        transition: 'box-shadow var(--tr), border-color var(--tr)',
      }}
      onMouseEnter={e => {
        const el = e.currentTarget as HTMLElement
        el.style.boxShadow = 'var(--shadow2)'
        el.style.borderColor = 'var(--border2)'
      }}
      onMouseLeave={e => {
        const el = e.currentTarget as HTMLElement
        el.style.boxShadow = 'var(--shadow)'
        el.style.borderColor = 'var(--border)'
      }}
    >
      {/* Card body */}
      <div className="flex-1 p-4">
        {/* Name */}
        <p className="text-sm font-semibold leading-snug mb-1.5" style={{ color: 'var(--t1)' }}>{item.name}</p>

        {/* Description */}
        {item.description ? (
          <p className="text-xs leading-relaxed" style={{ color: 'var(--t2)' }}>{item.description}</p>
        ) : (
          <p className="text-xs italic" style={{ color: 'var(--t3)' }}>暂无描述</p>
        )}
      </div>

      {/* Card footer */}
      <div className="px-4 py-3 flex items-center justify-between" style={{ borderTop: '1px solid var(--border)', background: 'var(--bg2)' }}>
        {item.create_time && (
          <span className="text-[10px]" style={{ color: 'var(--t3)' }}>
            {formatDate(item.create_time)}
          </span>
        )}
        <div className="ml-auto">
          {item.is_pulled ? (
            <span className="flex items-center gap-1 text-[11px] font-medium px-2.5 py-1 rounded-full" style={{ color: 'var(--green)', background: 'rgba(22,163,74,.08)' }}>
              <CheckCircle2Icon size={12} />已安装
            </span>
          ) : (
            <Button size="sm" variant="default" loading={pulling} onClick={onPull}>
              <DownloadIcon size={11} />安装
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Shared helpers ──────────────────────────────────────────────────────────

function EmptyState({ icon, title, desc, variant = 'default' }: {
  icon: React.ReactNode
  title: string
  desc: string
  variant?: 'default' | 'error'
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3">
      <div style={{ color: variant === 'error' ? 'var(--red)' : 'var(--t3)', opacity: .6 }}>{icon}</div>
      <p className="text-sm font-medium" style={{ color: variant === 'error' ? 'var(--red)' : 'var(--t2)' }}>{title}</p>
      <p className="text-xs text-center max-w-xs" style={{ color: 'var(--t3)' }}>{desc}</p>
    </div>
  )
}

function SkeletonCard({ tall = false }: { tall?: boolean }) {
  return (
    <div className="rounded-xl animate-pulse" style={{ background: 'var(--bg1)', border: '1px solid var(--border)', height: tall ? 140 : 72 }} />
  )
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('zh-CN', { year: 'numeric', month: 'short', day: 'numeric' })
  } catch {
    return ''
  }
}
