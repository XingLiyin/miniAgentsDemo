import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, BookOpen, ChevronDown, ChevronRight } from 'lucide-react'
import { skillSourceApi } from '@/api/skills'
import type {
  RemoteSkillSource,
  RegisterSkillSourceHttpRequest,
  RegisterSkillSourceStdioRequest,
} from '@/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog } from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/spinner'
import { clsx } from 'clsx'

type RegisterType = 'http' | 'stdio'

const TOOL_DEFAULTS = {
  mcp_tool_list_skills: 'listSkills',
  mcp_tool_load_skill_md: 'loadSkillMd',
  mcp_tool_get_skill_files: 'getSkillFiles',
  mcp_tool_load_skill_reference: 'loadSkillReference',
  mcp_tool_exec_skill_script: 'execSkillScript',
}

function RegisterSkillSourceDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [type, setType] = useState<RegisterType>('http')
  const [showAdvanced, setShowAdvanced] = useState(false)

  const [httpForm, setHttpForm] = useState<RegisterSkillSourceHttpRequest>({
    source_name: '',
    mcp_url: '',
    mcp_timeout: 30,
    ...TOOL_DEFAULTS,
  })
  const [stdioForm, setStdioForm] = useState<RegisterSkillSourceStdioRequest>({
    source_name: '',
    mcp_command: '',
    mcp_args: [],
    mcp_env: {},
    ...TOOL_DEFAULTS,
  })
  const [argsStr, setArgsStr] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})

  const mutation = useMutation({
    mutationFn: () => {
      if (type === 'http') return skillSourceApi.registerHttp(httpForm)
      return skillSourceApi.registerStdio({
        ...stdioForm,
        mcp_args: argsStr.trim() ? argsStr.split(' ') : [],
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skill-sources'] })
      onClose()
    },
  })

  function validate() {
    const errs: Record<string, string> = {}
    const form = type === 'http' ? httpForm : stdioForm
    if (!form.source_name.trim()) errs.source_name = '请输入名称'
    if (type === 'http' && !httpForm.mcp_url.trim()) errs.mcp_url = '请输入 URL'
    if (type === 'stdio' && !stdioForm.mcp_command.trim()) errs.mcp_command = '请输入命令'
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  function handleSubmit() {
    if (!validate()) return
    mutation.mutate()
  }

  const toolFields: { key: keyof typeof TOOL_DEFAULTS; label: string }[] = [
    { key: 'mcp_tool_list_skills', label: 'listSkills tool' },
    { key: 'mcp_tool_load_skill_md', label: 'loadSkillMd tool' },
    { key: 'mcp_tool_get_skill_files', label: 'getSkillFiles tool' },
    { key: 'mcp_tool_load_skill_reference', label: 'loadSkillReference tool' },
    { key: 'mcp_tool_exec_skill_script', label: 'execSkillScript tool' },
  ]

  const currentForm = type === 'http' ? httpForm : stdioForm
  const setCurrentForm = type === 'http'
    ? (v: Partial<RegisterSkillSourceHttpRequest>) => setHttpForm((f) => ({ ...f, ...v }))
    : (v: Partial<RegisterSkillSourceStdioRequest>) => setStdioForm((f) => ({ ...f, ...v }))

  return (
    <Dialog open={open} onClose={onClose} title="添加 Skill Source" size="md">
      <div className="flex flex-col gap-4">
        {/* Type switcher */}
        <div className="flex rounded-lg border border-gray-200 p-0.5 bg-gray-50 w-fit">
          {(['http', 'stdio'] as RegisterType[]).map((t) => (
            <button
              key={t}
              onClick={() => setType(t)}
              className={clsx(
                'px-4 py-1.5 rounded-md text-sm font-medium transition-colors',
                type === t ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
              )}
            >
              {t.toUpperCase()}
            </button>
          ))}
        </div>

        <Input
          label="名称 *"
          placeholder="my-skill-server"
          value={currentForm.source_name}
          onChange={(e) => setCurrentForm({ source_name: e.target.value })}
          error={errors.source_name}
        />

        {type === 'http' ? (
          <>
            <Input
              label="URL *"
              placeholder="http://localhost:3000/mcp"
              value={httpForm.mcp_url}
              onChange={(e) => setHttpForm((f) => ({ ...f, mcp_url: e.target.value }))}
              error={errors.mcp_url}
            />
            <Input
              label="超时（秒）"
              type="number"
              value={httpForm.mcp_timeout}
              onChange={(e) => setHttpForm((f) => ({ ...f, mcp_timeout: Number(e.target.value) }))}
            />
          </>
        ) : (
          <>
            <Input
              label="命令 *"
              placeholder="python"
              value={stdioForm.mcp_command}
              onChange={(e) => setStdioForm((f) => ({ ...f, mcp_command: e.target.value }))}
              error={errors.mcp_command}
            />
            <Input
              label="参数"
              placeholder="server.py --port 3000"
              value={argsStr}
              onChange={(e) => setArgsStr(e.target.value)}
              hint="空格分隔"
            />
          </>
        )}

        {/* Advanced: tool name overrides */}
        <div>
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 transition-colors"
          >
            {showAdvanced ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            高级：MCP tool 名称覆盖
          </button>
          {showAdvanced && (
            <div className="mt-3 flex flex-col gap-3 pl-3 border-l border-gray-100">
              {toolFields.map(({ key, label }) => (
                <Input
                  key={key}
                  label={label}
                  value={(currentForm as Record<string, unknown>)[key] as string}
                  onChange={(e) => setCurrentForm({ [key]: e.target.value })}
                />
              ))}
            </div>
          )}
        </div>

        {mutation.error instanceof Error && (
          <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-md">
            {mutation.error.message}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button onClick={handleSubmit} loading={mutation.isPending}>添加</Button>
        </div>
      </div>
    </Dialog>
  )
}

function SkillSourceRow({ source }: { source: RemoteSkillSource }) {
  const queryClient = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState(false)

  const deleteMutation = useMutation({
    mutationFn: () => skillSourceApi.delete(source.source_name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['skill-sources'] }),
  })

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 flex items-center gap-4">
      <div className="w-9 h-9 rounded-lg bg-emerald-50 flex items-center justify-center flex-shrink-0">
        <BookOpen size={18} className="text-emerald-600" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-gray-900 text-sm">{source.source_name}</span>
          <Badge variant={source.mcp_type === 'stdio' ? 'muted' : 'info'}>{source.mcp_type}</Badge>
        </div>
        <p className="text-xs text-gray-400 mt-0.5 truncate">
          {source.mcp_type === 'http' ? source.mcp_url : source.mcp_command}
        </p>
      </div>
      <div className="flex items-center gap-1 flex-shrink-0">
        {confirmDelete ? (
          <>
            <Button size="sm" variant="danger" loading={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>
              确认
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>取消</Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" className="text-gray-400 hover:text-red-500" onClick={() => setConfirmDelete(true)}>
            <Trash2 size={13} />
          </Button>
        )}
      </div>
    </div>
  )
}

export function SkillSourcesPage() {
  const [showAdd, setShowAdd] = useState(false)

  const { data: sources, isLoading, error } = useQuery({
    queryKey: ['skill-sources'],
    queryFn: skillSourceApi.list,
  })

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Skill Sources</h1>
          <p className="text-sm text-gray-500 mt-0.5">管理远端 Skill MCP 来源</p>
        </div>
        <Button onClick={() => setShowAdd(true)}>
          <Plus size={15} />
          添加 Skill Source
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12">
          <Spinner />
        </div>
      ) : error ? (
        <div className="text-sm text-red-600 bg-red-50 rounded-xl p-4">
          加载失败：{(error as Error).message}
        </div>
      ) : sources?.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          <BookOpen size={32} className="mx-auto mb-2 opacity-40" />
          <p className="text-sm">暂无 Skill Source</p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {sources?.map((s) => <SkillSourceRow key={s.source_name} source={s} />)}
        </div>
      )}

      <RegisterSkillSourceDialog open={showAdd} onClose={() => setShowAdd(false)} />
    </div>
  )
}
