import { http } from './client'

export interface LocalSkill {
  skill_id: string
  name: string
  description: string
  version: string
  triggers: string[]
}

export interface RemoteCatalogItem {
  id: string
  name: string
  description: string | null
  domain: string | null
  create_time: string | null
  is_pulled: boolean
}

export interface PullSkillResponse {
  skill_id: string
  name: string
}

export const skillsApi = {
  list:         () => http.get<LocalSkill[]>('/skills'),
  delete:       (skillId: string) => http.delete<void>(`/skills/${skillId}`),
  importLocal:  (file: File) => http.upload<LocalSkill>('/skills/import', file),

  catalog:      () => http.get<RemoteCatalogItem[]>('/skills/pull-server/catalog'),
  pull:         (remoteId: string, name: string) =>
    http.post<PullSkillResponse>(`/skills/pull-server/catalog/${remoteId}/pull`, { name }),
  importRemote: (file: File) => http.upload<PullSkillResponse>('/skills/pull-server/import', file),
}
