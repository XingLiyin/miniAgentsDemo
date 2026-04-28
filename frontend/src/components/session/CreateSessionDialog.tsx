import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { sessionsApi } from '@/api/sessions'
import { templatesApi } from '@/api/templates'
import { llmsApi } from '@/api/llms'
import type { CreateSessionRequest, InitialTaskConfig } from '@/types'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Dialog } from '@/components/ui/dialog'

interface Props {
  open: boolean
  onClose: () => void
  onCreated: (sessionId: string) => void
}

const DEFAULT_FORM: CreateSessionRequest = {
  user_prompt: '',
  template_id: null,
  token_budget: 200000,
  root_max_turns: 20,
  llm_name: null,
  llm_model: null,
  working_dir: null,
  initial_task: null,
}

export function CreateSessionDialog({ open, onClose, onCreated }: Props) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<CreateSessionRequest>(DEFAULT_FORM)
  const [goalError, setGoalError] = useState('')
  const [useSubagent, setUseSubagent] = useState(false)
  const [subagentTemplate, setSubagentTemplate] = useState('')

  const { data: templates } = useQuery({
    queryKey: ['templates'],
    queryFn: templatesApi.list,
  })

  const { data: llms } = useQuery({
    queryKey: ['llms'],
    queryFn: llmsApi.list,
  })

  const mutation = useMutation({
    mutationFn: sessionsApi.create,
    onSuccess: (session) => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
      onCreated(session.id)
      onClose()
      setForm(DEFAULT_FORM)
      setGoalError('')
      setUseSubagent(false)
      setSubagentTemplate('')
    },
  })

  function handleSubmit() {
    if (!form.user_prompt.trim()) {
      setGoalError('请描述 Agent 需要完成的任务')
      return
    }
    setGoalError('')
    const initialTask: InitialTaskConfig | null = useSubagent
      ? { use_subagent: true, subagent_template: subagentTemplate || null }
      : null
    mutation.mutate({ ...form, initial_task: initialTask })
  }

  return (
    <Dialog open={open} onClose={onClose} title="新建 Session" size="md">
      <div className="flex flex-col gap-4">
        <Textarea
          label="目标 (Goal) *"
          placeholder="请描述你希望 Agent 完成的任务，例如：分析这个代码库并生成文档..."
          value={form.user_prompt}
          onChange={(e) => setForm((f) => ({ ...f, user_prompt: e.target.value }))}
          rows={4}
          error={goalError}
        />

        <Select
          label="Agent 模板"
          value={form.template_id ?? ''}
          onChange={(e) =>
            setForm((f) => ({ ...f, template_id: e.target.value || null }))
          }
        >
          <option value="">使用默认模板</option>
          {templates?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} — {t.description || t.version}
            </option>
          ))}
        </Select>

        <Select
          label="Provider"
          value={form.llm_name ?? ''}
          onChange={(e) =>
            setForm((f) => ({ ...f, llm_name: e.target.value || null, llm_model: null }))
          }
        >
          <option value="">使用默认 Provider</option>
          {llms?.map((l) => (
            <option key={l.name} value={l.name}>{l.name}</option>
          ))}
        </Select>

        {form.llm_name && (() => {
          const provider = llms?.find(l => l.name === form.llm_name)
          if (!provider?.models.length) return null
          return (
            <Select
              label="模型"
              value={form.llm_model ?? ''}
              onChange={(e) =>
                setForm((f) => ({ ...f, llm_model: e.target.value || null }))
              }
            >
              <option value="">默认（{provider.default_model}）</option>
              {provider.models.map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
            </Select>
          )
        })()}

        <Input
          label="工作目录"
          placeholder="留空则使用服务器默认目录"
          value={form.working_dir ?? ''}
          onChange={(e) =>
            setForm((f) => ({ ...f, working_dir: e.target.value || null }))
          }
        />

        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Token 预算"
            type="number"
            value={form.token_budget}
            onChange={(e) =>
              setForm((f) => ({ ...f, token_budget: Number(e.target.value) }))
            }
          />
          <Input
            label="最大轮次"
            type="number"
            value={form.root_max_turns}
            onChange={(e) =>
              setForm((f) => ({ ...f, root_max_turns: Number(e.target.value) }))
            }
          />
        </div>

        {/* 首个 Task 执行方式 */}
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
            >
              <option value="">使用系统默认规划器</option>
              {templates?.map((t) => (
                <option key={t.id} value={t.name}>
                  {t.name} — {t.description || t.version}
                </option>
              ))}
            </Select>
          )}
        </div>

        {mutation.error instanceof Error && (
          <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-md">
            {mutation.error.message}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button onClick={handleSubmit} loading={mutation.isPending}>
            启动 Session
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
