import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { XIcon, Trash2Icon, TagIcon, DownloadIcon, CheckIcon } from 'lucide-react'
import { skillsApi } from '@/api/skills'
import type { LocalSkill, RemoteCatalogItem } from '@/api/skills'
import { Button } from '@/components/ui/button'

type Tab = 'local' | 'remote'

interface Props { open: boolean; onClose: () => void }

export function SkillsDialog({ open, onClose }: Props) {
  const [tab, setTab] = useState<Tab>('local')

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="flex max-h-[80vh] w-[560px] flex-col rounded-xl border border-gray-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <div className="flex gap-3">
            <TabButton active={tab === 'local'} onClick={() => setTab('local')}>本地 Skills</TabButton>
            <TabButton active={tab === 'remote'} onClick={() => setTab('remote')}>Skill 市场</TabButton>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700">
            <XIcon size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {tab === 'local' ? <LocalPanel /> : <RemotePanel />}
        </div>
      </div>
    </div>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`text-sm font-medium transition-colors ${active ? 'text-gray-900' : 'text-gray-400 hover:text-gray-600'}`}
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
      <div className="p-4">
        {isLoading ? (
          <p className="py-8 text-center text-sm text-gray-400">加载中…</p>
        ) : skills.length === 0 ? (
          <p className="py-8 text-center text-sm text-gray-400">暂无本地 Skill</p>
        ) : (
          <div className="flex flex-col gap-2">
            {skills.map(s => (
              <SkillCard key={s.skill_id} skill={s} onDelete={() => setConfirmId(s.skill_id)} />
            ))}
          </div>
        )}
      </div>

      {confirmId && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40">
          <div className="w-72 rounded-lg border border-gray-200 bg-white p-4 shadow-lg">
            <p className="mb-1 text-sm font-medium text-gray-900">删除 Skill</p>
            <p className="mb-4 text-xs text-gray-500">
              确认删除 <span className="font-mono font-medium text-gray-700">{confirmId}</span>？将同时删除对应目录，此操作不可撤销。
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
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium text-gray-900">{skill.name}</p>
            <span className="flex-shrink-0 rounded bg-gray-200 px-1.5 py-0.5 text-[10px] text-gray-600">
              v{skill.version}
            </span>
          </div>
          {skill.description && (
            <p className="mt-0.5 line-clamp-2 text-xs text-gray-500">{skill.description}</p>
          )}
          {skill.triggers.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {skill.triggers.map(t => (
                <span key={t} className="flex items-center gap-0.5 rounded bg-blue-50 px-1.5 py-0.5 text-[10px] text-blue-600">
                  <TagIcon size={9} />{t}
                </span>
              ))}
            </div>
          )}
        </div>
        <button onClick={onDelete} className="flex-shrink-0 text-gray-400 hover:text-red-500">
          <Trash2Icon size={13} />
        </button>
      </div>
    </div>
  )
}

// ── 远端 Skill 拉取 ────────────────────────────────────────────────────────

function RemotePanel() {
  const qc = useQueryClient()

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

  return (
    <div className="p-4">
      {isLoading ? (
        <p className="py-8 text-center text-sm text-gray-400">正在获取远端 Skill 列表…</p>
      ) : isError ? (
        <p className="py-8 text-center text-sm text-red-500">{(error as Error)?.message ?? '获取失败'}</p>
      ) : catalog.length === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400">远端暂无可用 Skill</p>
      ) : (
        <div className="flex flex-col gap-2">
          {catalog.map(item => (
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

function CatalogCard({
  item, pulling, onPull,
}: { item: RemoteCatalogItem; pulling: boolean; onPull: () => void }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium text-gray-900">{item.name}</p>
            {item.domain && (
              <span className="flex-shrink-0 rounded bg-gray-200 px-1.5 py-0.5 text-[10px] text-gray-600">
                {item.domain}
              </span>
            )}
          </div>
          {item.description && (
            <p className="mt-0.5 line-clamp-2 text-xs text-gray-500">{item.description}</p>
          )}
        </div>
        <div className="flex-shrink-0">
          {item.is_pulled ? (
            <span className="flex items-center gap-1 text-xs text-green-600">
              <CheckIcon size={12} />已拉取
            </span>
          ) : (
            <Button size="sm" variant="outline" loading={pulling} onClick={onPull}>
              <DownloadIcon size={12} />拉取
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
