import { http } from './client'
import type { AgentTemplate } from '@/types'

export const templatesApi = {
  list: (workspaceDir?: string) => {
    const params = workspaceDir ? `?workspace_dir=${encodeURIComponent(workspaceDir)}` : ''
    return http.get<AgentTemplate[]>(`/agent-templates${params}`)
  },
  get: (id: string) => http.get<AgentTemplate>(`/agent-templates/${id}`),
}
