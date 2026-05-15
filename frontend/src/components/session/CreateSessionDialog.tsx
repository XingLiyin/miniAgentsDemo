import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { templatesApi } from '@/api/templates'
import { llmsApi } from '@/api/llms'
import type { SessionConfig, InitialTaskConfig } from '@/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Dialog } from '@/components/ui/dialog'

interface Props {
  open: boolean
  onClose: () => void
  onConfigured: (config: SessionConfig) => void
}

const DEFAULT_CONFIG: SessionConfig = {
  template_id: null,
  token_budget: 0,
  llm_provider: null,
  llm_model: null,
  working_dir: null,
  initial_task: null,
}

export function CreateSessionDialog({ open, onClose, onConfigured }: Props) {
  const [config, setConfig] = useState<SessionConfig>(DEFAULT_CONFIG)
  const [useSubagent, setUseSubagent] = useState(false)
  const [subagentTemplate, setSubagentTemplate] = useState('')
  // 工作目录输入框的即时值，防抖后同步到 config
  const [workingDirInput, setWorkingDirInput] = useState('')
  const [debouncedWorkspaceDir, setDebouncedWorkspaceDir] = useState('')
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const isAbsolutePath = (p: string) => /^[/\\]|^[a-zA-Z]:/.test(p)
  const workingDirError = workingDirInput && isAbsolutePath(workingDirInput) ? '请填写相对路径，不支持绝对路径' : ''

  // 工作目录 debounce：停止输入 600ms 后更新 debouncedWorkspaceDir 并同步到 config
  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current)
    debounceTimer.current = setTimeout(() => {
      if (isAbsolutePath(workingDirInput)) return
      setDebouncedWorkspaceDir(workingDirInput)
      setConfig((c) => ({ ...c, working_dir: workingDirInput || null, template_id: null }))
    }, 600)
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
    }
  }, [workingDirInput])

  // 对话框关闭时重置状态
  useEffect(() => {
    if (!open) {
      setConfig(DEFAULT_CONFIG)
      setWorkingDirInput('')
      setDebouncedWorkspaceDir('')
      setUseSubagent(false)
      setSubagentTemplate('')
    }
  }, [open])

  const { data: templates, isFetching: templatesFetching } = useQuery({
    queryKey: ['templates', debouncedWorkspaceDir],
    queryFn: () => templatesApi.list(debouncedWorkspaceDir || undefined),
  })

  const { data: llms } = useQuery({
    queryKey: ['llms'],
    queryFn: llmsApi.list,
  })

  function handleConfirm() {
    const initialTask: InitialTaskConfig | null = useSubagent
      ? { use_subagent: true, subagent_template: subagentTemplate || null }
      : null
    onConfigured({ ...config, working_dir: workingDirInput || null, initial_task: initialTask })
    onClose()
  }

  return (
    <Dialog open={open} onClose={onClose} title="新建 Session" size="md">
      <div className="flex flex-col gap-4">

        {/* 工作目录：第一步 */}
        <Input
          label="工作目录"
          placeholder="如 my-project 或 team/proj-a"
          hint="填写相对路径，由服务器基于 WORKSPACE_BASE_DIR 解析；留空使用服务器默认目录"
          error={workingDirError}
          value={workingDirInput}
          onChange={(e) => setWorkingDirInput(e.target.value)}
        />

        {/* Agent 模板：依赖工作目录 */}
        <Select
          label={templatesFetching ? 'Agent 模板（加载中…）' : 'Agent 模板'}
          value={config.template_id ?? ''}
          onChange={(e) =>
            setConfig((c) => ({ ...c, template_id: e.target.value || null }))
          }
          disabled={templatesFetching}
        >
          <option value="">使用默认模板</option>
          {templates?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}{t.description ? ` — ${t.description}` : ''}
            </option>
          ))}
        </Select>

        <Select
          label="Provider"
          value={config.llm_provider ?? ''}
          onChange={(e) =>
            setConfig((c) => ({ ...c, llm_provider: e.target.value || null, llm_model: null }))
          }
        >
          <option value="">使用默认 Provider</option>
          {llms?.map((l) => (
            <option key={l.name} value={l.name}>{l.name}</option>
          ))}
        </Select>

        {config.llm_provider && (() => {
          const provider = llms?.find(l => l.name === config.llm_provider)
          if (!provider?.models.length) return null
          return (
            <Select
              label="模型"
              value={config.llm_model ?? ''}
              onChange={(e) =>
                setConfig((c) => ({ ...c, llm_model: e.target.value || null }))
              }
            >
              <option value="">默认（{provider.default_model}）</option>
              {provider.models.map(m => (
                <option key={m.name} value={m.name}>{m.name}</option>
              ))}
            </Select>
          )
        })()}

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Input
              label="Token 预算"
              type="number"
              value={config.token_budget}
              onChange={(e) =>
                setConfig((c) => ({ ...c, token_budget: Number(e.target.value) }))
              }
            />
            <p className="mt-1 text-[11px] text-gray-400">0 表示不限制输出 token；超出预算后 session 将自动终止</p>
          </div>
        </div>

        <div className="rounded-lg border border-gray-200 p-3 flex flex-col gap-3">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">首个任务</p>
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={useSubagent}
              onChange={(e) => {
                setUseSubagent(e.target.checked)
                if (!e.target.checked) setSubagentTemplate('')
              }}
              className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
            />
            <span className="text-sm text-gray-700">委托给规划 Sub-agent 执行</span>
          </label>
          {useSubagent && (
            <Select
              label="规划器模板"
              value={subagentTemplate}
              onChange={(e) => setSubagentTemplate(e.target.value)}
              disabled={templatesFetching}
            >
              <option value="">使用系统默认规划器</option>
              {templates?.map((t) => (
                <option key={t.id} value={t.name}>
                  {t.name}{t.description ? ` — ${t.description}` : ''}
                </option>
              ))}
            </Select>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button onClick={handleConfirm} disabled={!!workingDirError}>确认配置</Button>
        </div>
      </div>
    </Dialog>
  )
}
