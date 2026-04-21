import { http } from './client'
import type { LLMProvider, RegisterLLMRequest } from '@/types'

export const llmsApi = {
  list: () => http.get<LLMProvider[]>('/llms'),
  get: (name: string) => http.get<LLMProvider>(`/llms/${name}`),
  register: (data: RegisterLLMRequest) => http.post<LLMProvider>('/llms', data),
  delete: (name: string) => http.delete(`/llms/${name}`),
  addModel: (name: string, model: string) =>
    http.post<LLMProvider>(`/llms/${name}/models`, { model }),
  removeModel: (name: string, model: string) =>
    http.delete<LLMProvider>(`/llms/${name}/models`, { model }),
  setDefaultModel: (name: string, model: string) =>
    http.put<LLMProvider>(`/llms/${name}/default_model`, { model }),
}
