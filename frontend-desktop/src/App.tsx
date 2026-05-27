import { useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SessionList } from '@/components/SessionList'
import { ChatPanel } from '@/components/ChatPanel'
import { WorkspacePanel } from '@/components/WorkspacePanel'
import { SkillsPage } from '@/components/SkillsPage'
import { LLMSettingsPage } from '@/components/LLMSettingsPage'
import { useSessionSSE } from '@/hooks/useSessionSSE'
import type { PendingSession } from '@/types'

// ── 草稿持久化 ────────────────────────────────────────────────────────────────
// pendingSession 是 Smart B 阶段唯一不入后端的状态，关掉 app 就丢。
// 用 localStorage 落盘（Electron 下落在 %APPDATA%\NetLIVE-CoWork\Local Storage\）。

const PENDING_STORAGE_KEY = 'netlive.pendingSession.v1'

function loadPendingSession(): PendingSession | null {
  try {
    const raw = localStorage.getItem(PENDING_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    // 容错：确保关键字段存在
    if (typeof parsed?.workingDir === 'string' && parsed.workingDir) {
      return {
        workingDir: parsed.workingDir,
        provider: typeof parsed.provider === 'string' ? parsed.provider : '',
        model: typeof parsed.model === 'string' ? parsed.model : '',
      }
    }
    return null
  } catch {
    return null
  }
}

function savePendingSession(p: PendingSession | null): void {
  try {
    if (p) localStorage.setItem(PENDING_STORAGE_KEY, JSON.stringify(p))
    else localStorage.removeItem(PENDING_STORAGE_KEY)
  } catch {
    // localStorage 不可用或配额满 —— 忽略，不阻断 UI
  }
}

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
  // 启动时从 localStorage 恢复草稿
  const [pendingSession, setPendingSession] = useState<PendingSession | null>(loadPendingSession)
  const [centerView, setCenterView] = useState<CenterView>('chat')
  const [nextProvider, setNextProvider] = useState('')
  const [nextModel, setNextModel] = useState('')

  // 草稿任何变更都落盘
  useEffect(() => {
    savePendingSession(pendingSession)
  }, [pendingSession])

  const sse = useSessionSSE(centerView === 'chat' ? selectedId : null)
  // 草稿优先取自己的 workingDir；否则取选中会话的；都没有就空
  const draftActive = selectedId === null && pendingSession !== null
  const workingDir = draftActive
    ? pendingSession?.workingDir ?? ''
    : sse.session?.working_dir ?? ''

  // 切到已有会话：保留 pendingSession 不清空（修草稿丢失 bug）
  // 用户可通过 PendingSessionItem 切回草稿，或 X 显式取消
  function handleSelect(id: string) {
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

  // 点 PendingSessionItem：切回草稿视图（清 selectedId）
  function handlePendingSelect() {
    setSelectedId(null)
    setCenterView('chat')
  }

  // 显式取消草稿（X 按钮）
  function handleDismissDraft() {
    setPendingSession(null)
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
          onPendingSelect={handlePendingSelect}
          onDismissDraft={handleDismissDraft}
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
            // 仅当未选中已有会话时才传 pendingSession，避免 ChatPanel 在选中会话状态下进入 pending 渲染
            pendingSession={draftActive ? pendingSession : null}
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
