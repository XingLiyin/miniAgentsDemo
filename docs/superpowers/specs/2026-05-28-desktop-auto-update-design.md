# 桌面端自动更新 — 设计文档

- 日期：2026-05-28
- 状态：待评审
- 适用：NetLIVE-CoWork 桌面端（Electron + 内嵌 PyInstaller 后端）

## 1. 背景与目标

应用即将迁移到内网、面向多用户开放，当前为 beta，需要高频迭代与修复。由于是**桌面端分发**（每个用户本地运行一份 Electron 应用 + 本地后端实例，互不可达），没有共享多租户服务器，因此首要诉求是**能把新版本自动推达分散在各机器上的用户**，避免逐台手动重装。

目标：基于 `electron-updater` 实现自助更新，更新源为**自建内网 generic 静态文件服务器**，与首次安装下载共用同一托管。先在**单机用 localhost 静态服务器验证全链路**，生产仅替换 feed URL。

**不在本次范围**：OAuth 认证（独立、后做）、mac/Linux、自定义更新后端 / 下载统计、更新源的 per-user 鉴权。

## 2. 决策摘要

| 维度 | 决策 |
|---|---|
| 更新工具 | `electron-updater`（配合既有 `electron-builder` + NSIS target） |
| 更新源 | 自建内网 `generic` 静态文件服务器；先用 localhost 单机验证 |
| feed URL / channel | **运行时可配置**（env / AppData 配置文件），未配置则跳过检查、不报错 |
| 更新源鉴权 | **开放**，靠内网边界 + sha512 校验 + 建议 HTTPS/签名；不与 OAuth 耦合 |
| 分发 | 更新 feed 与首次安装下载**共用一个静态托管**，另加给人用的 "latest" 下载入口 |
| 代码仓 | **不单独建仓**；发布配置留在现有 app 仓；产物不入 git |
| 灰度发布 | **B 通道**（beta/stable）做定向 canary + `stagingPercentage` 做百分比 ramp |
| beta 入组 | **方案 A**：管理员手动给指定机器配 `channel=beta`，默认 stable，不做用户自助开关 |
| 更新 UX | 启动自动检查 + 后台自动下载 + 下载完非侵入提示「重启更新」+ 设置页手动「检查更新」按钮；安装由用户确认触发，不强制 |
| 数据迁移 | 版本感知补种（仅补缺失默认配置 / `.env` 新增键，绝不覆盖用户改动），与本次一起做 |
| 代码签名 | 推荐但 beta 不阻塞；未签名时仅首次安装弹 SmartScreen |
| 差分下载 | 开启 blockmap（PyInstaller 后端体积大） |

## 3. 架构总览

- 更新逻辑**完全在 Electron 主进程**（`electron-updater`）。Python 后端基本不动，只在两处与之协同：**安装前优雅停后端**、**升级后版本感知补种配置**。
- 分发（首次安装 + 更新）**共用一个静态文件托管**，产物为 `latest.yml`（stable）/ `beta.yml`（beta）+ 版本化 `Setup x.y.z.exe` + `.blockmap`，另加一个 "latest" 人用下载入口。
- channel 在 generic 静态 feed 下是**客户端侧设置**：客户端读 `{feedURL}/{channel}.yml`。服务器是哑文件托管，不决定（无鉴权下也无法决定）谁属于 beta。

## 4. 组件拆分

| 组件 | 位置 | 职责 |
|---|---|---|
| Updater 模块 | 新增 `electron/updater.js`（保持 `main.js` 精简） | 解析 feed URL + channel、接 electron-updater 事件、下载策略、触发安装 |
| Updater UI | `frontend-desktop` 设置页新增一块 | 当前版本、「检查更新」按钮、状态（检查中/有新版/下载 %/已下载/已是最新/出错）、「重启以更新」 |
| IPC 桥 | `electron/preload.js` | 暴露：触发检查、查状态、订阅进度事件、触发安装（已有 `app-version`） |
| 后端停止协调 | `electron/main.js`（强化现有 `stopBackend`） | 安装前确保 `netlive-cowork.exe` 真正退出、端口释放，带超时强杀兜底 |
| 版本感知补种 | `electron/main.js`（增强 `seedDefaultData` / `ensureUserEnvFile`） | 记 `installed_version`，版本变化时补缺失默认配置 + `.env` 新增键，绝不覆盖用户改动 |
| 发布配置 | `electron/package.json` build | 加 `publish: generic`、保留版本化产物 + blockmap、发布脚本 |
| 静态托管 | 基础设施（非 app 代码） | 共用于首装下载 + 更新 feed + "latest" 入口 |

## 5. 配置解析（feed URL + channel）

运行时按优先级解析，写在 `electron/updater.js`：

1. 环境变量 `NETLIVE_COWORK_UPDATE_FEED_URL` / `NETLIVE_COWORK_UPDATE_CHANNEL`；
2. AppData 配置文件 `%APPDATA%\NetLIVE-CoWork\update-config.json`，形如 `{ "feedUrl": "...", "channel": "beta" }`；
3. 打包时 electron-builder 写入的 `app-update.yml` 默认值（内网地址 / stable）；
4. feed URL 最终为空 → **直接跳过 `checkForUpdates()`，不发起任何请求**（零噪音）。

channel 取值：`beta` → `autoUpdater.channel = 'beta'`（读 `beta.yml`）；缺省 / `stable` → 沿用默认（读 `latest.yml`）。

约束：
- `autoUpdater` 仅在 `app.isPackaged` 下生效；dev 模式跳过（如需调事件接线，用 `dev-app-update.yml` + `autoUpdater.forceDevUpdateConfig = true`，但真正安装步骤仍需已装版）。
- **必须注册 `error` 事件监听**：服务器不可达时静默记日志、app 照常运行；不注册会因 Node EventEmitter 在 `error` 无监听时抛未捕获异常。

## 6. 更新流程（时序）

1. 应用启动并加载 UI；
2. 若 feed 已配置 → `autoUpdater.checkForUpdates()`；
3. 有新版 → 后台 `autoDownload`（默认开启），渲染层显示进度；
4. `update-downloaded` → 通知渲染层，提示「有新版本，重启以更新」；
5. 用户点确认 → **优雅停后端（见 §7）→ 端口释放确认**；
6. `autoUpdater.quitAndInstall(false, true)`（非静默、安装后自动重启）；
7. 重启为新版 → 启动时版本感知补种运行（见 §8）。

## 7. 后端停止时序（Windows 文件锁，关键风险）

`quitAndInstall` 前必须保证 spawn 出的 `netlive-cowork.exe` 已退出，否则 NSIS 覆盖安装会因文件被占用而失败。`stopBackend()` 升级为：

1. 向后端进程发 `SIGTERM`；
2. 轮询 `backendProcess.exitCode` / 设超时（如 5s）；
3. 超时仍未退出 → `taskkill /PID <pid> /T /F` 强杀进程树；
4. 处理「端口被占则复用已有后端」路径下可能存在的**孤儿后端**：检测端口仍被占用时定位并清理；
5. 确认端口释放后才进入 `quitAndInstall`。

## 8. 升级数据 / 配置迁移（版本感知补种）

- 用户数据在 `%APPDATA%\NetLIVE-CoWork`（`.env` / `data` / `logs` / `workspace`）。NSIS 更新只换安装目录、不动 AppData → **天然保留**（验证时眼见为实）。
- 补 gap：现状 `seedDefaultData` / `ensureUserEnvFile` 是「不存在才建」，升级时新版本新增的默认 llm/mcp 配置、`.env` 新增键，老用户拿不到。改为版本感知：
  - 在 AppData 记 `installed_version`；
  - 启动时比对当前 `app.getVersion()`：版本变化时，补入新版本新增的默认配置文件（**仅缺失项**）、合并 `.env` 新增键（**仅缺失键**）；
  - **已有内容一律不动**（不覆盖用户编辑）；
  - 更新 `installed_version`。
- 补种的 diff 逻辑抽成纯函数，便于单测（见 §13）。

## 9. 灰度发布（B 通道 + stagingPercentage）

### 9.1 通道机制
- 发布两条通道：stable → `latest.yml`，beta → `beta.yml`，各自指向对应版本化产物。
- 客户端通过 §5 的 channel 配置决定读哪条。

### 9.2 beta 入组（方案 A）
- 管理员手动给**指定机器**写 `update-config.json` 的 `channel: "beta"`（或设 env）。默认无该配置 = stable。
- 入组动作 = 在那几台机器写一下配置（手动 / 小脚本 / IT 托管机随провижн下发）。
- 人群由「谁能改这台机器的配置」界定，**无需登录**。

### 9.3 百分比 ramp
- `latest.yml` 内加 `stagingPercentage`（如 10），electron-updater 用各安装持久化的 staging GUID 算稳定随机值决定是否纳入本次更新。
- 放量 = 改这个数字：10 → 50 → 100，跨数天。纯清单字段，**无代码**。

### 9.4 典型放量路径
canary 几台配 beta 通道 → 验证一天 → 把产物 / yml 从 beta 复制到 stable 并以 `stagingPercentage` 逐步放量 → 100%。

### 9.5 重要 caveat（无鉴权的本质限制）
> channel 是**放量机制，不是访问控制**。无鉴权下，任何知道 beta 通道名 / 路径的人，把自己配成 `channel=beta` 即可拿到 beta 包。在内网、cohort 受控前提下没问题。若诉求是「**必须禁止**非 beta 用户拿到 beta 包」（如 beta 含未公开功能），需靠网络层限制或给 beta 通道单独加鉴权——属另一件事，与「灰度放量」分开。

## 10. 更新 UX

- 启动自动检查；后台自动下载（`autoDownload = true`）；下载完成非侵入提示「有新版本，重启以更新」。
- 设置页提供手动「检查更新」按钮与状态展示。
- 安装（重启）由用户确认触发，**不强制打断**。

## 11. 构建与发布流程

- 每次发版 bump `electron/package.json` 的 `version`；同时将后端 `app/config/settings.py` 的 `app_version`（`/health` 使用）对齐到同一来源。
- `electron-builder ... --publish`（手动起步，后续可挂 CI）产出并上传 `latest.yml` / `beta.yml` / `Setup x.y.z.exe` / `.blockmap`。
- 托管目录保留历史版本化文件（差分下载依赖）+ 维护 "latest" 人用下载入口。
- **portable target 不能自动更新**：更新能力仅针对 NSIS 安装版；portable 保留作免安装试用但不期望其更新。
- `stagingPercentage`、beta→stable 提升，均为发布流程的清单操作，无代码。

## 12. 单机验证计划（同时即开发循环）

1. `serve` / `python -m http.server` 起 localhost 文件源，`NETLIVE_COWORK_UPDATE_FEED_URL` 指向它；
2. build `0.1.0` → NSIS **正式安装**（必须用已装版，`app.isPackaged` 才生效）；
3. bump `0.1.1` → build → 产物丢进文件源目录；
4. 启动 `0.1.0` → 应检测 → 下载 → 重启为 `0.1.1`；
5. 重点验证：
   - 后端 exe 无文件锁失败；
   - `%APPDATA%` 数据（会话 / 配置）保留；
   - 版本感知补种生效（新增默认配置 / `.env` 键被补上，旧内容不变）；
   - blockmap 差分下载生效；
   - channel：把 `update-config.json` 配 `beta`，验证读取 `beta.yml`。

## 13. 测试策略

- `electron-updater` 本身难做单元测试 → 主要靠 §12 单机验证清单（手动，写进实现计划）。
- 可单测的纯逻辑：
  - **feed URL / channel 解析**（env → update-config.json → bundled → skip）；
  - **版本感知补种 diff**（仅补缺失、不覆盖）。

## 14. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Windows 后端 exe 文件锁导致覆盖失败 | §7 强制停后端 + 超时强杀 + 端口释放确认 |
| 更新服务器未就绪 / 不可达 | §5 可配置 + 未配跳过 + 必接 `error` handler |
| 未签名触发 SmartScreen | 签名列为推荐项，beta 不阻塞；仅首装受影响 |
| 全量包体积大 | 开启 blockmap 差分下载 |
| 升级后老用户缺新默认配置 | §8 版本感知补种 |
| 误把 dev/portable 当可更新 | §5 `app.isPackaged` 守卫；portable 不期望更新 |

## 15. 不在本次范围

代码签名（推荐但可选）、mac/Linux、自定义更新后端、灰度按身份精确「禁止访问」（仅做放量）、下载统计 / 管理后台、更新源 per-user 鉴权、OAuth 认证（独立后做）。

## 16. 未决 / 可调整项

- 更新 UX 默认采「后台自动下载 + 提示」，如需改为「下载前征询」可调。
- `stagingPercentage` 的具体 ramp 节奏（如 10/50/100 的天数）待发布时定。
- 后端 `app_version` 与 `electron/package.json` `version` 的「同一来源」具体落地方式（构建时注入 vs 手动同步）待实现时定。
