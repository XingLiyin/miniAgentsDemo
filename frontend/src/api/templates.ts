import { http } from './client'
import type { AgentTemplate } from '@/types'

export const templatesApi = {
  list: () => http.get<AgentTemplate[]>('/agent-templates'),
  get: (id: string) => http.get<AgentTemplate>(`/agent-templates/${id}`),
}
