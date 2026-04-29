import { useState } from 'react'
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
  token_budget: 200000,
  root_max_turns: 20,
  llm_name: null,
  llm_model: null,
  working_dir: null,
  initial_task: null,
}

export function CreateSessionDialog({ open, onClose, onConfigured }: Props) {
  const [config, setConfig] = useState<SessionConfig>(DEFAULT_CONFIG)
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

  function handleConfirm() {
    const initialTask: InitialTaskConfig | null = useSubagent
      ? { use_subagent: true, subagent_template: subagentTemplate || null }
      : null
    onConfigured({ ...config, initial_task: initialTask })
    onClose()
    setConfig(DEFAULT_CONFIG)
    setUseSubagent(false)
    setSubagentTemplate('')
  }

  return (
    <Dialog open={open} onClose={onClose} title="新建 Session" size="md">
      <div className="flex flex-col gap-4">
        <Select
          label="Agent 模板"
          value={config.template_id ?? ''}
          onChange={(e) =>
            setConfig((c) => ({ ...c, template_id: e.target.value || null }))
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
          value={config.llm_name ?? ''}
          onChange={(e) =>
            setConfig((c) => ({ ...c, llm_name: e.target.value || null, llm_model: null }))
          }
        >
          <option value="">使用默认 Provider</option>
          {llms?.map((l) => (
            <option key={l.name} value={l.name}>{l.name}</option>
          ))}
        </Select>

        {config.llm_name && (() => {
          const provider = llms?.find(l => l.name === config.llm_name)
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

        <Input
          label="工作目录"
          placeholder="留空则使用服务器默认目录"
          value={config.working_dir ?? ''}
          onChange={(e) =>
            setConfig((c) => ({ ...c, working_dir: e.target.value || null }))
          }
        />

        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Token 预算"
            type="number"
            value={config.token_budget}
            onChange={(e) =>
              setConfig((c) => ({ ...c, token_budget: Number(e.target.value) }))
            }
          />
          <Input
            label="最大轮次"
            type="number"
            value={config.root_max_turns}
            onChange={(e) =>
              setConfig((c) => ({ ...c, root_max_turns: Number(e.target.value) }))
            }
          />
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

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button onClick={handleConfirm}>确认配置</Button>
        </div>
      </div>
    </Dialog>
  )
}
