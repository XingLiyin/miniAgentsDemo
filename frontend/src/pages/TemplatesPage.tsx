import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, Wrench, Sparkles, ChevronRight, X } from 'lucide-react'
import { templatesApi } from '@/api/templates'
import type { AgentTemplate } from '@/types'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/spinner'

function TemplateDrawer({
  template,
  onClose,
}: {
  template: AgentTemplate
  onClose: () => void
}) {
  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/30" onClick={onClose} />
      <div className="fixed right-0 top-0 h-full w-[560px] bg-white shadow-2xl z-50 flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h2 className="text-base font-semibold text-gray-900">{template.name}</h2>
            <p className="text-xs text-gray-400 mt-0.5">v{template.version}</p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-5">
          {template.description && (
            <p className="text-sm text-gray-600">{template.description}</p>
          )}

          {/* Metadata */}
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-gray-50 rounded-lg p-3">
              <p className="text-xs text-gray-400 mb-1">摘要阈值</p>
              <p className="text-sm font-medium text-gray-800">{template.summary_threshold} 条消息</p>
            </div>
            <div className="bg-gray-50 rounded-lg p-3">
              <p className="text-xs text-gray-400 mb-1">上下文窗口</p>
              <p className="text-sm font-medium text-gray-800">{template.short_window_size} 条消息</p>
            </div>
            <div className="bg-gray-50 rounded-lg p-3">
              <p className="text-xs text-gray-400 mb-1">可生成子 Agent</p>
              <p className="text-sm font-medium text-gray-800">
                {template.has_spawn_permission ? '是' : '否'}
              </p>
            </div>
            <div className="bg-gray-50 rounded-lg p-3">
              <p className="text-xs text-gray-400 mb-1">注入风格 Prompt</p>
              <p className="text-sm font-medium text-gray-800">
                {template.inject_style ? '是' : '否'}
              </p>
            </div>
          </div>

          {/* Tool list */}
          {template.tool_list.length > 0 && (
            <div>
              <p className="text-xs font-medium text-gray-500 mb-2">工具列表</p>
              <div className="flex flex-wrap gap-1.5">
                {template.tool_list.map((tool) => (
                  <span
                    key={tool}
                    className="inline-flex items-center gap-1 px-2 py-1 bg-gray-100 rounded text-xs font-mono text-gray-700"
                  >
                    <Wrench size={10} />
                    {tool}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* System prompt */}
          <div>
            <p className="text-xs font-medium text-gray-500 mb-2">System Prompt 预览</p>
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 max-h-96 overflow-y-auto">
              <pre className="text-xs text-gray-700 whitespace-pre-wrap font-sans leading-relaxed">
                {template.system_prompt || '（System Prompt 尚未生成）'}
              </pre>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

function TemplateCard({
  template,
  onView,
}: {
  template: AgentTemplate
  onView: () => void
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 flex flex-col gap-3 hover:border-blue-200 hover:shadow-sm transition-all">
      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-50 to-purple-50 flex items-center justify-center flex-shrink-0">
          <Bot size={20} className="text-blue-600" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-gray-900">{template.name}</h3>
            <span className="text-xs text-gray-400">v{template.version}</span>
          </div>
          {template.description && (
            <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{template.description}</p>
          )}
        </div>
      </div>

      {/* Tool chips */}
      {template.tool_list.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {template.tool_list.slice(0, 4).map((tool) => (
            <span
              key={tool}
              className="px-1.5 py-0.5 bg-gray-100 rounded text-xs font-mono text-gray-600"
            >
              {tool}
            </span>
          ))}
          {template.tool_list.length > 4 && (
            <span className="px-1.5 py-0.5 bg-gray-100 rounded text-xs text-gray-400">
              +{template.tool_list.length - 4}
            </span>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between pt-1 border-t border-gray-100">
        <div className="flex items-center gap-2">
          {template.has_spawn_permission && (
            <Badge variant="info">
              <Sparkles size={10} />
              可生成子 Agent
            </Badge>
          )}
          {!template.tool_list_ready && (
            <Badge variant="warning">工具列表待解析</Badge>
          )}
        </div>
        <Button size="sm" variant="ghost" onClick={onView}>
          查看 <ChevronRight size={12} />
        </Button>
      </div>
    </div>
  )
}

export function TemplatesPage() {
  const [selected, setSelected] = useState<AgentTemplate | null>(null)

  const { data: templates, isLoading, error } = useQuery({
    queryKey: ['templates'],
    queryFn: templatesApi.list,
  })

  return (
    <div className="p-6">
      <div className="max-w-4xl mx-auto">
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-gray-900">Agent Templates</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            从磁盘自动加载，只读。修改请编辑 SOUL.md / ROLE.md 文件并重启服务。
          </p>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-12">
            <Spinner />
          </div>
        ) : error ? (
          <div className="text-sm text-red-600 bg-red-50 rounded-xl p-4">
            加载失败：{(error as Error).message}
          </div>
        ) : templates?.length === 0 ? (
          <div className="text-center py-12 text-gray-400">
            <Bot size={32} className="mx-auto mb-2 opacity-50" />
            <p className="text-sm">暂无模板。请在 agents_dir 目录下创建模板文件夹。</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {templates?.map((t) => (
              <TemplateCard key={t.id} template={t} onView={() => setSelected(t)} />
            ))}
          </div>
        )}
      </div>

      {selected && (
        <TemplateDrawer template={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}
