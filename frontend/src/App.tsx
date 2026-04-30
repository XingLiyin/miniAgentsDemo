import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Layout } from '@/components/layout/Layout'
import { SessionsPage } from '@/pages/SessionsPage'
import { LLMsPage } from '@/pages/LLMsPage'
import { MCPPage } from '@/pages/MCPPage'
import { SkillSourcesPage } from '@/pages/SkillSourcesPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000,
      retry: 1,
    },
  },
})

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<Navigate to="/sessions" replace />} />
            <Route path="/sessions" element={<SessionsPage />} />
            <Route path="/llms" element={<LLMsPage />} />
            <Route path="/mcp" element={<MCPPage />} />
            <Route path="/skills" element={<SkillSourcesPage />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
