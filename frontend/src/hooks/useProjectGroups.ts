/**
 * useProjectGroups —— 把 sessions 按 working_dir 聚合成 Project 列表。
 *
 * 当前阶段（Smart B）：前端聚合，无后端实体。
 * 未来升级到方案 C 时：换成调 /api/v1/projects API 即可，UI 完全不动。
 */

import { useMemo } from 'react'
import type { Session, Project } from '@/types'
import { NO_PROJECT_ID } from '@/types'

function pathParts(wd: string): string[] {
  return wd.split(/[\\/]/).filter(Boolean)
}

/**
 * 生成项目显示名：
 *   - 空 working_dir → '无项目'
 *   - basename 在所有项目中唯一 → 直接用 basename
 *   - basename 冲突 → 逐级往上加路径段消歧，直到唯一为止
 */
function buildDisplayName(wd: string, allWds: Set<string>): string {
  if (!wd) return '无项目'
  const myParts = pathParts(wd)
  const others = Array.from(allWds)
    .filter(w => w && w !== wd)
    .map(pathParts)
  for (let depth = 1; depth <= myParts.length; depth++) {
    const tail = myParts.slice(-depth).join('/')
    const conflict = others.some(p => p.slice(-depth).join('/') === tail)
    if (!conflict) return tail
  }
  return wd
}

export function useProjectGroups(sessions: Session[]): Project[] {
  return useMemo(() => {
    const groups = new Map<string, Session[]>()
    for (const s of sessions) {
      const id = s.working_dir || NO_PROJECT_ID
      const list = groups.get(id) ?? []
      list.push(s)
      groups.set(id, list)
    }
    const allWds = new Set(
      Array.from(groups.keys()).filter(k => k !== NO_PROJECT_ID)
    )
    const projects: Project[] = []
    for (const [id, sess] of groups) {
      const wd = id === NO_PROJECT_ID ? '' : id
      const sorted = [...sess].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      projects.push({
        id,
        display_name: buildDisplayName(wd, allWds),
        working_dir: wd,
        sessions: sorted,
        session_count: sorted.length,
        last_accessed_at: sorted[0]?.updated_at ?? '',
      })
    }
    // 最近访问的项目在前，"无项目" 永远在最后
    projects.sort((a, b) => {
      if (a.id === NO_PROJECT_ID) return 1
      if (b.id === NO_PROJECT_ID) return -1
      return b.last_accessed_at.localeCompare(a.last_accessed_at)
    })
    return projects
  }, [sessions])
}

/**
 * 从一组 sessions 中找到匹配某 working_dir 的最近会话，提取其默认配置。
 * 用于 CreateSessionDialog 的"自动套用最近设置"。
 */
export function pickDefaultsFromRecentSession(
  sessions: Session[],
  workingDir: string,
): { session: Session; defaults: NonNullable<Project['defaults']> } | null {
  if (!workingDir) return null
  const matches = sessions
    .filter(s => s.working_dir === workingDir)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  const session = matches[0]
  if (!session) return null
  return {
    session,
    defaults: {
      llm_provider: session.llm_provider,
      llm_model: session.llm_model,
      template_id: session.template_id,
      token_budget: session.token_budget,
    },
  }
}
