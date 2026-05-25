import { http } from './client'
import type { Session, CreateSessionRequest } from '@/types'

export type TextPart  = { type: 'text'; text: string }
export type ImagePart = { type: 'image'; data: string; media_type: string; source_type: 'base64' | 'url' }
export type MessageContent = string | (TextPart | ImagePart)[]

export const sessionsApi = {
  list:      () => http.get<Session[]>('/sessions'),
  get:       (id: string) => http.get<Session>(`/sessions/${id}`),
  create:    (data: CreateSessionRequest) => http.post<Session>('/sessions', data),
  cancel:    (id: string) => http.post<Session>(`/sessions/${id}/cancel`),
  interrupt: (id: string) => http.post<Session>(`/sessions/${id}/interrupt`),
  delete:    (id: string) => http.delete<void>(`/sessions/${id}`),
  sendMessage: (
    id: string,
    content: MessageContent,
    llmProvider?: string | null,
    llmModel?: string | null,
  ) =>
    http.post<Session>(`/sessions/${id}/messages`, {
      content,
      llm_provider: llmProvider ?? null,
      llm_model: llmModel ?? null,
    }),
  answerInput: (id: string, content: string) =>
    http.post<Session>(`/sessions/${id}/input`, { content }),
}
