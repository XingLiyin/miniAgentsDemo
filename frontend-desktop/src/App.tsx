import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SessionList } from '@/components/SessionList'
import { ChatPanel } from '@/components/ChatPanel'
import { WorkspacePanel } from '@/components/WorkspacePanel'
import { SkillsPage } from '@/components/SkillsPage'
import { LLMSettingsPage } from '@/components/LLMSettingsPage'
import { useSessionSSE } from '@/hooks/useSessionSSE'
import type { PendingSession } from '@/types'

export type CenterView = 'chat' | 'skills' | 'llm'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 1000, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Desktop />
    </QueryClientProvider>
  )
}

function Desktop() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pendingSession, setPendingSession] = useState<PendingSession | null>(null)
  const [centerView, setCenterView] = useState<CenterView>('chat')
  const [nextProvider, setNextProvider] = useState('')
  const [nextModel, setNextModel] = useState('')

  const sse = useSessionSSE(centerView === 'chat' ? selectedId : null)
  const workingDir = pendingSession?.workingDir ?? sse.session?.working_dir ?? ''

  function handleSelect(id: string) {
    setPendingSession(null)
    setSelectedId(id)
    setCenterView('chat')
  }

  function handleNewSession(pending: PendingSession) {
    setSelectedId(null)
    setPendingSession({
      ...pending,
      provider: pending.provider || nextProvider,
      model: pending.model || nextModel,
    })
    setCenterView('chat')
  }

  function handleSessionCreated(id: string) {
    setPendingSession(null)
    setSelectedId(id)
  }

  function handleNextLLMChange(provider: string, model: string) {
    setNextProvider(provider)
    setNextModel(model)
    if (pendingSession) {
      setPendingSession({ ...pendingSession, provider, model })
    }
  }

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg0)', color: 'var(--t1)' }}>
      {/* Left: Session list + nav */}
      <div className="w-60 flex-shrink-0" style={{ background: 'var(--bg1)', borderRight: '1px solid var(--border)' }}>
        <SessionList
          selectedId={centerView === 'chat' ? selectedId : null}
          pendingSession={centerView === 'chat' ? pendingSession : null}
          centerView={centerView}
          onViewChange={setCenterView}
          onSelect={handleSelect}
          onNewSession={handleNewSession}
          onPendingSelect={() => setCenterView('chat')}
        />
      </div>

      {/* Center */}
      <div className="flex min-w-0 flex-1 flex-col" style={{ background: 'var(--bg0)' }}>
        {centerView === 'skills' ? (
          <SkillsPage />
        ) : centerView === 'llm' ? (
          <LLMSettingsPage />
        ) : (
          <ChatPanel
            sessionId={selectedId}
            sse={sse}
            pendingSession={pendingSession}
            onSessionCreated={handleSessionCreated}
            nextProvider={nextProvider}
            nextModel={nextModel}
            onNextLLMChange={handleNextLLMChange}
          />
        )}
      </div>

      {/* Right: Workspace (only shown when a session with working_dir is selected) */}
      {centerView === 'chat' && selectedId && workingDir && (
        <div className="w-72 flex-shrink-0" style={{ background: 'var(--bg1)', borderLeft: '1px solid var(--border)' }}>
          <WorkspacePanel workingDir={workingDir} />
        </div>
      )}
    </div>
  )
}
