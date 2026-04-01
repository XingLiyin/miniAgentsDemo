import { http } from './client'
import type { Session, CreateSessionRequest, Task } from '@/types'

export const sessionsApi = {
  list: () => http.get<Session[]>('/sessions'),
  get: (id: string) => http.get<Session>(`/sessions/${id}`),
  create: (data: CreateSessionRequest) => http.post<Session>('/sessions', data),
  cancel: (id: string) => http.post<Session>(`/sessions/${id}/cancel`),
  getTasks: (id: string) => http.get<Task[]>(`/sessions/${id}/tasks`),
  sendMessage: (id: string, content: string) =>
    http.post<Session>(`/sessions/${id}/messages`, { content }),
  answerInput: (id: string, task_id: string, content: string) =>
    http.post<Session>(`/sessions/${id}/input`, { task_id, content }),
}
