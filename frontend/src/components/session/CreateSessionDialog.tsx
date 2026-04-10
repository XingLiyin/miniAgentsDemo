import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { sessionsApi } from '@/api/sessions'
import { templatesApi } from '@/api/templates'
import { llmsApi } from '@/api/llms'
import type { CreateSessionRequest } from '@/types'
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

export function CreateSessionDialog({ open, onClose, onCreated }: Props) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<CreateSessionRequest>({
    goal: '',
    template_id: null,
    token_budget: 200000,
    root_max_turns: 20,
    llm_name: null,
  })
  const [goalError, setGoalError] = useState('')

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
      setForm({ goal: '', template_id: null, token_budget: 200000, root_max_turns: 20, llm_name: null })
      setGoalError('')
    },
  })

  function handleSubmit() {
    if (!form.goal.trim()) {
      setGoalError('请描述 Agent 需要完成的任务')
      return
    }
    setGoalError('')
    mutation.mutate(form)
  }

  return (
    <Dialog open={open} onClose={onClose} title="新建 Session" size="md">
      <div className="flex flex-col gap-4">
        <Textarea
          label="目标 (Goal) *"
          placeholder="请描述你希望 Agent 完成的任务，例如：分析这个代码库并生成文档..."
          value={form.goal}
          onChange={(e) => setForm((f) => ({ ...f, goal: e.target.value }))}
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
          label="模型"
          value={form.llm_name ?? ''}
          onChange={(e) =>
            setForm((f) => ({ ...f, llm_name: e.target.value || null }))
          }
        >
          <option value="">使用默认模型</option>
          {llms?.map((l) => (
            <option key={l.name} value={l.name}>
              {l.name} — {l.model}
            </option>
          ))}
        </Select>

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
