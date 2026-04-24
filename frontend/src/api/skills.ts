import { http } from './client'
import type {
  RemoteSkillSource,
  RegisterSkillSourceHttpRequest,
  RegisterSkillSourceStdioRequest,
} from '@/types'

export const skillSourceApi = {
  list: () => http.get<RemoteSkillSource[]>('/remote-skill-sources'),
  get: (name: string) => http.get<RemoteSkillSource>(`/remote-skill-sources/${name}`),
  registerHttp: (data: RegisterSkillSourceHttpRequest) =>
    http.post<RemoteSkillSource>('/remote-skill-sources/http', data),
  registerStdio: (data: RegisterSkillSourceStdioRequest) =>
    http.post<RemoteSkillSource>('/remote-skill-sources/stdio', data),
  delete: (name: string) => http.delete(`/remote-skill-sources/${name}`),
}
