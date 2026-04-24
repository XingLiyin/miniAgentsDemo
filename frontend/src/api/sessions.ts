import { http } from './client'
import type { Session, CreateSessionRequest, InitialTaskConfig, Task } from '@/types'

export type TextPart = { type: 'text'; text: string }
export type ImagePart = { type: 'image'; data: string; media_type: string; source_type: 'base64' | 'url' }
export type ContentPart = TextPart | ImagePart
export type MessageContent = string | ContentPart[]

export const sessionsApi = {
  list: () => http.get<Session[]>('/sessions'),
  get: (id: string) => http.get<Session>(`/sessions/${id}`),
  create: (data: CreateSessionRequest) => http.post<Session>('/sessions', data),
  cancel: (id: string) => http.post<Session>(`/sessions/${id}/cancel`),
  getTasks: (id: string) => http.get<Task[]>(`/sessions/${id}/tasks`),
  sendMessage: (id: string, content: MessageContent, initialTask?: InitialTaskConfig | null) =>
    http.post<Session>(`/sessions/${id}/messages`, { content, initial_task: initialTask ?? null }),
  answerInput: (id: string, content: string) =>
    http.post<Session>(`/sessions/${id}/input`, { content }),
  delete: (id: string) => http.delete<void>(`/sessions/${id}`),
}
