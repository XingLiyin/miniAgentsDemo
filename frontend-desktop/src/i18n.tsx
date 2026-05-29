/**
 * 轻量 i18n —— React Context + t() + localStorage 持久化。
 *
 * 用法：
 *   const { t, lang, setLang } = useI18n()
 *   t('sidebar.sessions')
 *   t('workspace.items', { count: 3 })   // 插值 {count}
 *
 * 词典以 zh 为基准（现有中文），en 为翻译。缺失键回退到 zh，再回退到 key 本身。
 */

import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

export type Lang = 'zh' | 'en'

export const LANGUAGES: { value: Lang; label: string }[] = [
  { value: 'zh', label: '简体中文' },
  { value: 'en', label: 'English' },
]

const STORAGE_KEY = 'netlive.lang.v1'

type Dict = Record<string, string>

const zh: Dict = {
  // common
  'common.cancel': '取消',
  'common.delete': '删除',
  'common.confirm': '确认',
  'common.back': '返回',
  'common.retry': '重试',
  'common.loading': '加载中…',
  'common.close': '关闭',

  // file preview
  'filePreview.unsupported': '不支持预览此文件类型（{ext}）',
  'filePreview.unknownExt': '未知',
  'filePreview.empty': '文件为空',

  // sidebar
  'sidebar.sessions': '会话',
  'sidebar.newSession': '新建会话',
  'sidebar.noSessions': '暂无会话，点击 + 新建',
  'sidebar.settings': '设置',
  'sidebar.skillMarket': 'Skill 市场',
  'sidebar.llmConfig': 'LLM 配置',
  'sidebar.noProject': '未指定目录',
  'sidebar.deleteSessionConfirm': '确认删除这个会话？此操作不可撤销。',
  'sidebar.draftWaiting': '等待第一条消息…',
  'sidebar.dismissDraft': '放弃这个未发送的草稿',
  'sidebar.createInProject': '在 {name} 项目内新建会话',

  // settings popup
  'settings.language': '语言',
  'settings.version': '版本',

  // session status
  'status.QUEUED': '等待中',
  'status.RUNNING': '运行中',
  'status.WAITING_INPUT': '等待输入',
  'status.PAUSED_HITL': '暂停',
  'status.SUCCEEDED': '完成',
  'status.FAILED': '失败',
  'status.CANCELED': '已取消',
  'status.INTERRUPTED': '已中断',

  // misc
  'misc.connectionLost': '连接断开，正在重连…',

  // chat
  'chat.inputPlaceholder': '输入消息（Enter 发送，Shift+Enter 换行）',
  'chat.firstInputPlaceholder': '输入第一条消息（Enter 发送）',
  'chat.defaultModel': '默认模型',
  'chat.startAgentHint': '发送第一条消息来启动 Agent',
  'chat.hideWorkspace': '隐藏工作区',
  'chat.showWorkspace': '打开工作区',
  'chat.copy': '复制',
  'chat.copied': '已复制',
  'chat.me': '我',
  'chat.startConversation': '开始对话',
  'chat.selectOrCreate': '选择或新建一个会话',
  'chat.waitingResponse': '等待 Agent 响应…',
  'chat.toolFailed': '✕ 失败',
  'chat.toolDone': '✓ 完成',
  'chat.args': '参数：',
  'chat.error': '错误：',
  'chat.result': '结果：',
  'chat.resultTruncated': '（结果已截断）',
  'chat.reasoning': '推理过程',
  'chat.execConfirm': '执行确认',
  'chat.allow': '允许',
  'chat.reject': '拒绝',
  'chat.agentNeedsInput': 'Agent 需要你的输入',
  'chat.replyPlaceholder': '输入你的回复…',
  'chat.send': '发送',

  // new session dialog
  'newSession.title': '新建会话',
  'newSession.workingDir': '工作目录',
  'newSession.selectDir': '点击选择目录…',
  'newSession.model': '模型（可选）',
  'newSession.useDefault': '使用默认',
  'newSession.create': '创建会话',
  'newSession.appliedRecent': '已套用此目录最近会话的模型（{id}…）',
  'newSession.dirNeedsElectron': '目录选择需要在 Electron 客户端中使用',

  // workspace panel
  'workspace.title': '工作区',
  'workspace.items': '{count} 项',
  'workspace.openInExplorer': '在文件管理器中打开',
  'workspace.refresh': '刷新',
  'workspace.close': '关闭工作区',
  'workspace.backToParent': '返回上级',
  'workspace.empty': '目录为空',
  'workspace.notConfigured': '工作区未配置',
  'workspace.folders': '{count} 个文件夹',
  'workspace.files': '{count} 个文件',

  // skills page
  'skills.title': 'Skills',
  'skills.localTab': '本地 Skills',
  'skills.marketTab': 'Skill 市场',
  'skills.importZip': '导入 zip',
  'skills.uploadRemote': '上传到远端',
  'skills.emptyLocalTitle': '暂无本地 Skill',
  'skills.emptyLocalDesc': '前往 Skill 市场下载 Skills 到本地使用',
  'skills.deleteTitle': '删除 Skill',
  'skills.deleteConfirmPre': '确认删除 ',
  'skills.deleteConfirmPost': '？',
  'skills.deleteConfirmNote': '将同时删除对应目录，此操作不可撤销。',
  'skills.searchPlaceholder': '搜索 Skill 名称、描述或分类…',
  'skills.fetchFailed': '获取失败',
  'skills.fetchFailedDesc': '无法连接到 Skill 服务器，请检查网络连接',
  'skills.emptyRemoteTitle': '远端暂无可用 Skill',
  'skills.emptyRemoteDesc': '稍后再来查看',
  'skills.noMatchTitle': '未找到匹配的 Skill',
  'skills.noMatchDesc': '没有与 "{q}" 相关的结果',
  'skills.noDescription': '暂无描述',
  'skills.installed': '已安装',
  'skills.install': '安装',

  // LLM settings page
  'llm.title': 'LLM 配置',
  'llm.addProvider': '添加大模型',
  'llm.emptyTitle': '尚未配置任何大模型',
  'llm.emptyDesc': '添加大模型后即可在对话中使用',
  'llm.deleteTitle': '删除大模型',
  'llm.deleteConfirmPre': '确认删除 ',
  'llm.deleteConfirmPost': '？',
  'llm.deleteConfirmNote': '关联的模型配置将一并删除，此操作不可撤销。',
  'llm.openaiCompat': 'OpenAI 兼容',
  'llm.models': '模型',
  'llm.testConnection': '测试连通性',
  'llm.modelNamePlaceholder': '输入模型名称，验证后添加',
  'llm.verifyAndAdd': '验证并添加',
  'llm.fetchModelsList': '从接口获取可用模型列表',
  'llm.availableModels': '可用模型',
  'llm.name': '名称',
  'llm.namePlaceholder': '供应商名称，如：OpenAI',
  'llm.type': '类型',
  'llm.authEndpoint': '认证与端点',
  'llm.baseUrlOptional': 'Base URL（可选）',
  'llm.defaultHint': '点击 ★ 设为默认；首个模型自动设为默认。',
  'llm.saveProvider': '保存大模型',
  'llm.connectFailed': '连接失败',
  'llm.fetchFailed': '获取失败',
}

const en: Dict = {
  // common
  'common.cancel': 'Cancel',
  'common.delete': 'Delete',
  'common.confirm': 'Confirm',
  'common.back': 'Back',
  'common.retry': 'Retry',
  'common.loading': 'Loading…',
  'common.close': 'Close',

  // file preview
  'filePreview.unsupported': 'Preview not supported for this file type ({ext})',
  'filePreview.unknownExt': 'unknown',
  'filePreview.empty': 'File is empty',

  // sidebar
  'sidebar.sessions': 'Sessions',
  'sidebar.newSession': 'New Session',
  'sidebar.noSessions': 'No sessions yet — click + to create',
  'sidebar.settings': 'Settings',
  'sidebar.skillMarket': 'Skill Market',
  'sidebar.llmConfig': 'LLM Providers',
  'sidebar.noProject': 'No directory',
  'sidebar.deleteSessionConfirm': 'Delete this session? This cannot be undone.',
  'sidebar.draftWaiting': 'Waiting for the first message…',
  'sidebar.dismissDraft': 'Discard this unsent draft',
  'sidebar.createInProject': 'New session in project {name}',

  // settings popup
  'settings.language': 'Language',
  'settings.version': 'Version',

  // session status
  'status.QUEUED': 'Queued',
  'status.RUNNING': 'Running',
  'status.WAITING_INPUT': 'Waiting',
  'status.PAUSED_HITL': 'Paused',
  'status.SUCCEEDED': 'Done',
  'status.FAILED': 'Failed',
  'status.CANCELED': 'Canceled',
  'status.INTERRUPTED': 'Interrupted',

  // misc
  'misc.connectionLost': 'Connection lost, reconnecting…',

  // chat
  'chat.inputPlaceholder': 'Type a message (Enter to send, Shift+Enter for newline)',
  'chat.firstInputPlaceholder': 'Type your first message (Enter to send)',
  'chat.defaultModel': 'Default model',
  'chat.startAgentHint': 'Send the first message to start the Agent',
  'chat.hideWorkspace': 'Hide workspace',
  'chat.showWorkspace': 'Show workspace',
  'chat.copy': 'Copy',
  'chat.copied': 'Copied',
  'chat.me': 'Me',
  'chat.startConversation': 'Start a conversation',
  'chat.selectOrCreate': 'Select or create a session',
  'chat.waitingResponse': 'Waiting for the Agent…',
  'chat.toolFailed': '✕ Failed',
  'chat.toolDone': '✓ Done',
  'chat.args': 'Args:',
  'chat.error': 'Error:',
  'chat.result': 'Result:',
  'chat.resultTruncated': '(result truncated)',
  'chat.reasoning': 'Reasoning',
  'chat.execConfirm': 'Execution Confirmation',
  'chat.allow': 'Allow',
  'chat.reject': 'Reject',
  'chat.agentNeedsInput': 'The Agent needs your input',
  'chat.replyPlaceholder': 'Type your reply…',
  'chat.send': 'Send',

  // new session dialog
  'newSession.title': 'New Session',
  'newSession.workingDir': 'Working Directory',
  'newSession.selectDir': 'Click to select a directory…',
  'newSession.model': 'Model (optional)',
  'newSession.useDefault': 'Use default',
  'newSession.create': 'Create Session',
  'newSession.appliedRecent': "Applied the model from this directory's recent session ({id}…)",
  'newSession.dirNeedsElectron': 'Directory selection requires the Electron client',

  // workspace panel
  'workspace.title': 'Workspace',
  'workspace.items': '{count} items',
  'workspace.openInExplorer': 'Open in file explorer',
  'workspace.refresh': 'Refresh',
  'workspace.close': 'Close workspace',
  'workspace.backToParent': 'Back to parent',
  'workspace.empty': 'Directory is empty',
  'workspace.notConfigured': 'Workspace not configured',
  'workspace.folders': '{count} folder(s)',
  'workspace.files': '{count} file(s)',

  // skills page
  'skills.title': 'Skills',
  'skills.localTab': 'Local Skills',
  'skills.marketTab': 'Skill Market',
  'skills.importZip': 'Import zip',
  'skills.uploadRemote': 'Upload to remote',
  'skills.emptyLocalTitle': 'No local skills',
  'skills.emptyLocalDesc': 'Go to the Skill Market to download skills for local use',
  'skills.deleteTitle': 'Delete Skill',
  'skills.deleteConfirmPre': 'Delete ',
  'skills.deleteConfirmPost': '?',
  'skills.deleteConfirmNote': 'Its directory will also be removed. This cannot be undone.',
  'skills.searchPlaceholder': 'Search skills by name, description or category…',
  'skills.fetchFailed': 'Failed to load',
  'skills.fetchFailedDesc': 'Cannot reach the Skill server — please check your network',
  'skills.emptyRemoteTitle': 'No skills available remotely',
  'skills.emptyRemoteDesc': 'Check back later',
  'skills.noMatchTitle': 'No matching skills',
  'skills.noMatchDesc': 'No results related to "{q}"',
  'skills.noDescription': 'No description',
  'skills.installed': 'Installed',
  'skills.install': 'Install',

  // LLM settings page
  'llm.title': 'LLM Providers',
  'llm.addProvider': 'Add Provider',
  'llm.emptyTitle': 'No providers configured yet',
  'llm.emptyDesc': 'Add a provider to use it in conversations',
  'llm.deleteTitle': 'Delete Provider',
  'llm.deleteConfirmPre': 'Delete ',
  'llm.deleteConfirmPost': '?',
  'llm.deleteConfirmNote': 'Its model configuration will be removed too. This cannot be undone.',
  'llm.openaiCompat': 'OpenAI-compatible',
  'llm.models': 'Models',
  'llm.testConnection': 'Test connectivity',
  'llm.modelNamePlaceholder': 'Enter a model name, then verify to add',
  'llm.verifyAndAdd': 'Verify & add',
  'llm.fetchModelsList': 'Fetch available models from the API',
  'llm.availableModels': 'Available models',
  'llm.name': 'Name',
  'llm.namePlaceholder': 'Provider name, e.g. OpenAI',
  'llm.type': 'Type',
  'llm.authEndpoint': 'Authentication & Endpoint',
  'llm.baseUrlOptional': 'Base URL (optional)',
  'llm.defaultHint': 'Click ★ to set default; the first model is the default.',
  'llm.saveProvider': 'Save Provider',
  'llm.connectFailed': 'Connection failed',
  'llm.fetchFailed': 'Failed to load',
}

const DICTS: Record<Lang, Dict> = { zh, en }

function detectInitial(): Lang {
  // 1) 用户手动选过的优先
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'zh' || saved === 'en') return saved
  } catch { /* ignore */ }
  // 2) 首次启动跟随系统/UI 语言（Electron 下 navigator.language 反映 Windows 语言）
  try {
    const sys = (navigator.languages?.[0] || navigator.language || '').toLowerCase()
    if (sys.startsWith('zh')) return 'zh'
    if (sys) return 'en'   // 仅有中英两种，非中文系统统一回退英文
  } catch { /* ignore */ }
  // 3) 兜底
  return 'zh'
}

interface I18nContextValue {
  lang: Lang
  setLang: (l: Lang) => void
  t: (key: string, vars?: Record<string, string | number>) => string
}

const I18nContext = createContext<I18nContextValue | null>(null)

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectInitial)

  const setLang = useCallback((l: Lang) => {
    setLangState(l)
    try { localStorage.setItem(STORAGE_KEY, l) } catch { /* ignore */ }
  }, [])

  const t = useCallback((key: string, vars?: Record<string, string | number>) => {
    let s = DICTS[lang][key] ?? zh[key] ?? key
    if (vars) {
      for (const k of Object.keys(vars)) {
        s = s.replace(new RegExp(`\\{${k}\\}`, 'g'), String(vars[k]))
      }
    }
    return s
  }, [lang])

  return <I18nContext.Provider value={{ lang, setLang, t }}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext)
  if (!ctx) throw new Error('useI18n must be used within LanguageProvider')
  return ctx
}
