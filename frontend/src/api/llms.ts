import { http } from './client'
import type { LLMProvider, RegisterLLMRequest } from '@/types'

export const llmsApi = {
  list: () => http.get<LLMProvider[]>('/llms'),
  get: (name: string) => http.get<LLMProvider>(`/llms/${name}`),
  register: (data: RegisterLLMRequest) => http.post<LLMProvider>('/llms', data),
  delete: (name: string) => http.delete(`/llms/${name}`),
}
