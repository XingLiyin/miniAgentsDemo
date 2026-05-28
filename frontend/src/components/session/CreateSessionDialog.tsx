import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { templatesApi } from '@/api/templates'
import { llmsApi } from '@/api/llms'
import type { SessionConfig, InitialTaskConfig, Session } from '@/types'
import { pickDefaultsFromRecentSession } from '@/hooks/useProjectGroups'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Dialog } from '@/components/ui/dialog'

interface Props {
  open: boolean
  initialWorkingDir?: string         // 从某项目"新建会话"时预填
  recentSessions?: Session[]         // 用于 datalist 建议 + 套用最近设置
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

const isAbsolutePath = (p: string) => /^[/\\]|^[a-zA-Z]:/.test(p)

export function CreateSessionDialog({ open, initialWorkingDir = '', recentSessions = [], onClose, onConfigured }: Props) {
  const [config, setConfig] = useState<SessionConfig>(DEFAULT_CONFIG)
  const [useSubagent, setUseSubagent] = useState(false)
  const [subagentTemplate, setSubagentTemplate] = useState('')
  // 工作目录输入框的即时值，防抖后同步到 config
  const [workingDirInput, setWorkingDirInput] = useState('')
  const [debouncedWorkspaceDir, setDebouncedWorkspaceDir] = useState('')
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 自动套用提示：显示套用了哪个会话的设置
  const [appliedFromSession, setAppliedFromSession] = useState<string>('')
  // 最新 recentSessions 引用（让 applyWdChange 不必加进 effect deps）
  const recentSessionsRef = useRef(recentSessions)
  useEffect(() => { recentSessionsRef.current = recentSessions }, [recentSessions])

  // datalist 候选：去重已有会话的 working_dir
  const wdSuggestions = useMemo(() => {
    const set = new Set<string>()
    for (const s of recentSessions) {
      if (s.working_dir) set.add(s.working_dir)
    }
    return Array.from(set).sort()
  }, [recentSessions])

  const workingDirError = workingDirInput && isAbsolutePath(workingDirInput) ? '请填写相对路径，不支持绝对路径' : ''

  /**
   * 工作目录变化时的统一处理：同步 debouncedWorkspaceDir + 套用最近会话设置。
   * 集中在一处避免多个 effect 互相覆盖（如 template_id 被先填后清）。
   * 字段填充策略：
   *   - template_id：wd 变了模板列表也会变，直接用 pick.template_id（如有），否则置 null
   *   - 其他字段：仅当用户未手动设置时（仍为默认值）才填充，尊重已有选择
   */
  const applyWdChange = useCallback((wd: string) => {
    setDebouncedWorkspaceDir(wd)
    const pick = wd ? pickDefaultsFromRecentSession(recentSessionsRef.current, wd) : null
    setConfig((c) => {
      const next: SessionConfig = { ...c, working_dir: wd || null }
      next.template_id = pick?.defaults.template_id ?? null
      if (pick) {
        if (c.llm_provider === null && pick.defaults.llm_provider) next.llm_provider = pick.defaults.llm_provider
        if (c.llm_model === null && pick.defaults.llm_model) next.llm_model = pick.defaults.llm_model
        if ((c.token_budget ?? 0) === 0 && pick.defaults.token_budget) next.token_budget = pick.defaults.token_budget
      }
      return next
    })
    setAppliedFromSession(pick ? pick.session.id : '')
  }, [])

  // 工作目录 debounce：停止输入 600ms 后统一处理（不再单独 setConfig）
  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current)
    debounceTimer.current = setTimeout(() => {
      if (isAbsolutePath(workingDirInput)) return
      applyWdChange(workingDirInput)
    }, 600)
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
    }
  }, [workingDirInput, applyWdChange])

  // 对话框关闭时重置；打开且有 initialWorkingDir 时立即套用（不等防抖）
  useEffect(() => {
    if (!open) {
      setConfig(DEFAULT_CONFIG)
      setWorkingDirInput('')
      setDebouncedWorkspaceDir('')
      setUseSubagent(false)
      setSubagentTemplate('')
      setAppliedFromSession('')
    } else if (initialWorkingDir) {
      setWorkingDirInput(initialWorkingDir)
      applyWdChange(initialWorkingDir)
    }
  }, [open, initialWorkingDir, applyWdChange])

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
        <div>
          <Input
            label="工作目录"
            placeholder="如 my-project 或 team/proj-a"
            hint="填写相对路径，由服务器基于 WORKSPACE_BASE_DIR 解析；留空使用服务器默认目录"
            error={workingDirError}
            value={workingDirInput}
            onChange={(e) => setWorkingDirInput(e.target.value)}
            list="wd-suggestions"
          />
          {wdSuggestions.length > 0 && (
            <datalist id="wd-suggestions">
              {wdSuggestions.map((wd) => (
                <option key={wd} value={wd} />
              ))}
            </datalist>
          )}
          {appliedFromSession && (
            <p className="mt-1 text-[11px] text-blue-500">
              已套用最近会话的 LLM / 模板配置（来自 {appliedFromSession.slice(0, 12)}…）
            </p>
          )}
        </div>

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
