import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, AlertTriangle, KeyRound, CheckCircle, Cpu } from 'lucide-react'
import { llmsApi } from '@/api/llms'
import type { LLMProvider, RegisterLLMRequest, LLMStyle } from '@/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Dialog } from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/spinner'

function RegisterLLMDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<RegisterLLMRequest>({
    name: '',
    style: 'openai',
    api_key: '',
    base_url: '',
    model: '',
    timeout_sec: 60,
  })
  const [errors, setErrors] = useState<Partial<Record<keyof RegisterLLMRequest, string>>>({})

  const mutation = useMutation({
    mutationFn: llmsApi.register,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llms'] })
      onClose()
      setForm({ name: '', style: 'openai', api_key: '', base_url: '', model: '', timeout_sec: 60 })
      setErrors({})
    },
  })

  function validate() {
    const errs: typeof errors = {}
    if (!form.name.trim()) errs.name = '请输入名称'
    if (!form.api_key.trim()) errs.api_key = '请输入 API Key'
    if (!form.model.trim()) errs.model = '请输入模型名称'
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  function handleSubmit() {
    if (!validate()) return
    const payload = { ...form }
    if (!payload.base_url) delete payload.base_url
    mutation.mutate(payload)
  }

  const modelPlaceholder = form.style === 'openai' ? 'gpt-4o' : 'claude-sonnet-4-6'

  return (
    <Dialog open={open} onClose={onClose} title="注册 LLM Provider" size="md">
      <div className="flex flex-col gap-4">
        <Input
          label="名称 *"
          placeholder="my-gpt4o"
          value={form.name}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          error={errors.name}
        />
        <Select
          label="类型 *"
          value={form.style}
          onChange={(e) => setForm((f) => ({ ...f, style: e.target.value as LLMStyle }))}
        >
          <option value="openai">OpenAI 兼容</option>
          <option value="anthropic">Anthropic</option>
        </Select>
        <Input
          label="API Key *"
          type="password"
          placeholder="sk-..."
          value={form.api_key}
          onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
          error={errors.api_key}
          hint="提交后不可查看"
        />
        <Input
          label="Base URL"
          placeholder="留空使用默认（https://api.openai.com/v1）"
          value={form.base_url ?? ''}
          onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
        />
        <Input
          label="模型 *"
          placeholder={modelPlaceholder}
          value={form.model}
          onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
          error={errors.model}
        />
        <Input
          label="超时（秒）"
          type="number"
          value={form.timeout_sec}
          onChange={(e) => setForm((f) => ({ ...f, timeout_sec: Number(e.target.value) }))}
        />
        {mutation.error instanceof Error && (
          <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-md">
            {mutation.error.message}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button onClick={handleSubmit} loading={mutation.isPending}>注册</Button>
        </div>
      </div>
    </Dialog>
  )
}

function LLMCard({ provider }: { provider: LLMProvider }) {
  const queryClient = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState(false)

  const deleteMutation = useMutation({
    mutationFn: () => llmsApi.delete(provider.name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llms'] })
    },
  })

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 flex items-start gap-4">
      <div className="w-9 h-9 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0">
        <Cpu size={18} className="text-blue-600" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-gray-900 text-sm">{provider.name}</span>
          <Badge variant="info">{provider.style}</Badge>
        </div>
        <p className="text-xs text-gray-500 mt-0.5">{provider.model}</p>
        <div className="flex items-center gap-1 mt-1 text-xs text-gray-400">
          <KeyRound size={11} />
          <span>API Key 已配置</span>
          <span className="mx-1">·</span>
          <span>超时 {provider.timeout_sec}s</span>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <CheckCircle size={14} className="text-green-500" />
        {confirmDelete ? (
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="danger"
              loading={deleteMutation.isPending}
              onClick={() => deleteMutation.mutate()}
            >
              确认删除
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
              取消
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="text-gray-400 hover:text-red-500"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 size={13} />
          </Button>
        )}
      </div>
    </div>
  )
}

export function LLMsPage() {
  const [showRegister, setShowRegister] = useState(false)

  const { data: providers, isLoading, error } = useQuery({
    queryKey: ['llms'],
    queryFn: llmsApi.list,
  })

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">LLM Providers</h1>
          <p className="text-sm text-gray-500 mt-0.5">管理 AI 模型服务连接</p>
        </div>
        <Button onClick={() => setShowRegister(true)}>
          <Plus size={15} />
          注册 Provider
        </Button>
      </div>

      {!isLoading && providers?.length === 0 && (
        <div className="flex items-start gap-3 bg-yellow-50 border border-yellow-200 rounded-xl p-4 mb-5">
          <AlertTriangle size={16} className="text-yellow-600 mt-0.5 flex-shrink-0" />
          <div>
            <p className="text-sm font-medium text-yellow-800">尚未配置 LLM Provider</p>
            <p className="text-xs text-yellow-700 mt-0.5">
              至少需要注册一个 Provider 才能启动 Session。
            </p>
          </div>
          <Button size="sm" onClick={() => setShowRegister(true)} className="ml-auto flex-shrink-0">
            立即配置
          </Button>
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-12">
          <Spinner />
        </div>
      ) : error ? (
        <div className="text-sm text-red-600 bg-red-50 rounded-xl p-4">
          加载失败：{(error as Error).message}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {providers?.map((p) => <LLMCard key={p.name} provider={p} />)}
        </div>
      )}

      <RegisterLLMDialog open={showRegister} onClose={() => setShowRegister(false)} />
    </div>
  )
}
