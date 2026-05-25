import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { XIcon, FolderOpenIcon } from 'lucide-react'
import { llmsApi } from '@/api/llms'
import { ModelPickerButton } from '@/components/ui/ModelPickerButton'
import { Button } from '@/components/ui/button'
import type { PendingSession } from '@/types'

declare global {
  interface Window {
    electronAPI?: {
      selectDirectory: () => Promise<string | null>
      openPath: (p: string) => Promise<void>
    }
  }
}

interface Props {
  open: boolean
  onClose: () => void
  onCreated: (pending: PendingSession) => void
}

export function NewSessionDialog({ open, onClose, onCreated }: Props) {
  const [workingDir, setWorkingDir] = useState('')
  const [selProvider, setSelProvider] = useState('')
  const [selModel, setSelModel] = useState('')

  const { data: providers = [] } = useQuery({ queryKey: ['llms'], queryFn: llmsApi.list })

  async function pickDirectory() {
    if (window.electronAPI) {
      const dir = await window.electronAPI.selectDirectory()
      if (dir) setWorkingDir(dir)
    } else {
      alert('目录选择需要在 Electron 客户端中使用')
    }
  }

  function handleCreate() {
    if (!workingDir.trim()) return
    onCreated({ workingDir: workingDir.trim(), provider: selProvider, model: selModel })
    setWorkingDir('')
    setSelProvider('')
    setSelModel('')
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(15,31,61,.35)', backdropFilter: 'blur(4px)' }}>
      <div className="w-[420px]" style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 16, boxShadow: '0 24px 80px rgba(15,31,61,.18)' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <h2 className="text-sm font-semibold" style={{ color: 'var(--t1)' }}>新建会话</h2>
          <button onClick={onClose} style={{ color: 'var(--t3)', background: 'none', border: 'none', cursor: 'pointer' }}>
            <XIcon size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex flex-col gap-4 p-4">
          {/* Working dir — required, picker only */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium" style={{ color: 'var(--t2)' }}>工作目录 <span className="text-red-500">*</span></label>
            <div
              onClick={pickDirectory}
              className="flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm"
              style={{ border: '1px solid var(--border)', background: 'var(--bg2)', transition: 'background var(--tr)' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--bg3)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'var(--bg2)' }}
            >
              <FolderOpenIcon size={14} className="flex-shrink-0 text-yellow-500" />
              {workingDir ? (
                <span className="min-w-0 flex-1 truncate" style={{ color: 'var(--t1)' }}>{workingDir}</span>
              ) : (
                <span style={{ color: 'var(--t3)' }}>点击选择目录…</span>
              )}
            </div>
          </div>

          {/* Model picker */}
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium" style={{ color: 'var(--t2)' }}>模型（可选）</label>
            <ModelPickerButton
              variant="field"
              providers={providers}
              selectedProvider={selProvider}
              selectedModel={selModel}
              onChange={(p, m) => { setSelProvider(p); setSelModel(m) }}
              placeholder="使用默认"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-4 py-3" style={{ borderTop: '1px solid var(--border)' }}>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button disabled={!workingDir.trim()} onClick={handleCreate}>
            创建会话
          </Button>
        </div>
      </div>
    </div>
  )
}
