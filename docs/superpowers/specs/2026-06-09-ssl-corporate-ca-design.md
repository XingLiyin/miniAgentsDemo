# 企业 SSL 检测代理 CA 自动信任 — 设计方案

**日期**: 2026-06-09
**模块**: `app/common/ssl_verify.py`（重写第 3 级）+ 五个调用点
**状态**: 设计已批准，待写实现计划

## 问题

公司内网的 SSL 深度检测代理（SSL Inspection Proxy）用**企业 CA 签名的证书**替换真实服务器证书。Python 用 OpenSSL 验证证书，certifi 包里没有这个企业 CA，导致连接 LLM API 时报 `CERTIFICATE_VERIFY_FAILED`。

Windows 原生应用（Chrome、Outlook 等）没问题，因为它们用 Windows Schannel，Schannel 会在 TLS 握手时通过 AIA 等机制按需获取缺失的 CA。Python/OpenSSL 不走这套机制，所以暴露了缺口。

**根因**：企业 CA 通过 AD/AIA 动态下发，没写进 Windows 注册表标准路径（0.2.53 实测：注册表企业/GPO 路径全空）。

## 历史教训

0.2.48–0.2.55 绕了七八个版本，根因是一开始就锚定了**错误前提**："应用启动时提前把企业 CA 装进 SSLContext"。这个前提逼出了"探针"——在没有真实连接时凭空找一张证书去解析。每一版都在给探针打补丁（truststore → ctypes → winreg → CertGetCertificateChain → 探针连 openai → 代理隧道 → 换 microsoft.com），从没质疑探针本身。

**关键洞察**（用户提出）：测 LLM 配置**本来就是**真实握手，代理会主动把企业 CA 签发的证书发过来——不需要提前准备，等真实连接失败时现抓现下即可，这才是 Schannel 真正做的事。

## 设计原则

- **反应式而非主动**：只在真实连接 cert-verify 失败时才触发抓取，针对调用方**实际的 URL**，不再用 microsoft.com/openai.com 这类猜测的探针地址。
- **由近及远、层层兜底**：先用握手已送的链 → 再环境变量 → 最后才 AIA 下载。AIA 从"唯一手段"降为"最后兜底"。
- **一次抓取，全局复用**：抓到的 CA 持久化到 AppData，喂给所有调用点和后续重启。

## 三级分流逻辑

| 级别 | 条件 | 行为 |
|------|------|------|
| 1 | `http_ssl_verify=false` | 返回 `False`，跳过验证（不安全，逃生口） |
| 2 | `http_ca_bundle` 已配置 | 解析**多条目**（分号分隔，每条是文件路径 **或** http(s) URL）→ 下载/读取 → 加载 |
| 3 | 都没配 | 基础上下文 + 反应式 AIA 抓取（本方案核心） |

### 第 2 级：`http_ca_bundle` 多条目

```
IPMASTER_COWORK_HTTP_CA_BUNDLE=http://pki.corp.com/sub-ca.crt;http://pki.corp.com/root-ca.crt
# 或文件路径，或混用
IPMASTER_COWORK_HTTP_CA_BUNDLE=C:\certs\corp-ca.crt;http://pki.corp.com/root.crt
```

- 分号分隔多条目
- 每条以 `http://`/`https://` 开头 → HTTP GET 下载（AIA 端点通常是纯 HTTP），下载结果也写入 AppData 缓存
- 否则当本地文件路径读取
- 用户只需从浏览器/`certmgr.msc` 查看代理证书的 AIA 字段，复制 CA Issuers URL 填入 `.env`，不必手动下载文件

## 第 3 级架构

### 获取链（由近及远）

```
基础上下文 = certifi 公网根
           + Windows 注册表证书（保留现有 _load_registry_certs）
           + 环境变量 CA（SSL_CERT_FILE / SSL_CERT_DIR / REQUESTS_CA_BUNDLE / NODE_EXTRA_CA_CERTS）
           + AppData 已缓存的企业 CA
                    ↓ 仍 cert-verify 失败时，反应式触发
反应式抓取（针对调用方实际 URL）:
  1. 不验证地连接该 URL，get_unverified_chain() 拿代理呈现的【完整链】（叶子 + 中间）
  2. 链不完整（缺中间或根）时，用 cryptography 解析 AIA 的 CA Issuers URL，HTTP 下载，沿链向上递归到根
  3. 抓到的 CA 先放入【内存待定层 _PENDING_CA_DERS】（不立即落盘）
  4. make_ssl_verify.cache_clear() → 重建上下文（含待定层）→ 重试该请求一次
  5. cache-on-success：仅当重试【验证成功】才把这些 CA 写入 AppData 持久化；
     重试仍失败则回滚内存待定层，绝不持久化（防止被投毒的 AIA HTTP 响应把伪造 CA
     变成跨重启、跨所有连接的永久信任锚）
```

**为什么需要内存待定层**：cache-on-success 意味着重试发生时 CA 还没落盘，但重试必须先信任这些
CA 才能验证成功。基础上下文从 AppData 加载，看不到尚未落盘的 CA。因此引入进程内
`_PENDING_CA_DERS`：`_build_base_context` 同时加载 AppData 缓存 + 待定层。这也顺带修复了
"流式 ping 路径的 do_request 忽略 verify、只靠全局 make_ssl_verify() 取证书"的脆弱点——
两条路径（post 注入 verify / ping 走全局）都能通过重建后的全局上下文拿到待定 CA。

### 组件（各自单一职责）

1. **`_build_base_context() -> ssl.SSLContext`**
   - `PROTOCOL_TLS_CLIENT`，`check_hostname=True`，`verify_mode=CERT_REQUIRED`
   - `ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)`（公网根）
   - Windows：`_load_registry_certs(ctx)`（保留现有实现）
   - `_load_env_var_cas(ctx)`（新）：读 `SSL_CERT_FILE`/`SSL_CERT_DIR`/`REQUESTS_CA_BUNDLE`/`NODE_EXTRA_CA_CERTS` 指向的文件并加载
   - `_load_cached_cas(ctx)`（新）：加载 AppData 缓存目录下所有 CA
   - **删除**坏掉的 Phase 2 探针 + `CertGetCertificateChain` ctypes 代码

2. **`_load_cached_cas(ctx)` / `_save_ca_to_cache(der) -> Path`**
   - 缓存目录：`%APPDATA%\IPMaster-Cowork\ssl_certs\`（非 Windows 用对应的 user data dir）
   - 文件名 = 证书 SHA-256 指纹（去重），内容为 DER
   - 构建上下文时把目录下所有证书加载进去

3. **`_get_leaf_and_chain(url) -> list[bytes]`**（改造现有 `_get_leaf_cert`）
   - 保留系统代理 CONNECT 隧道逻辑（`urllib.request.getproxies()`）+ 直连兜底
   - 不验证握手后调用 `ssock.get_unverified_chain()` 返回**整条链的 DER 列表**（Python 3.10+）；回退到 `getpeercert(binary_form=True)` 仅取叶子
   - 只在反应式抓取时调用，URL 来自调用方

4. **`_fetch_corporate_ca_chain(url) -> list[bytes]`**（新核心）
   - 调 `_get_leaf_and_chain(url)` 拿握手链
   - 用 `cryptography.x509` 解析每张证书：检查是否已能构成到自签根的完整链
   - 缺失部分：读 AIA 扩展（`AuthorityInformationAccess` → `OID ca_issuers`）的 URL，HTTP GET 下载（支持 DER 和 PEM），解析后继续沿其 AIA 向上，直到自签根或无 AIA
   - 每张抓到的 CA 调 `_save_ca_to_cache`
   - 返回所有抓到的 DER 列表

5. **`with_ssl_retry(do_request, url)`**（新共享反应式包装器）
   ```python
   def with_ssl_retry(do_request, url):
       try:
           return do_request(make_ssl_verify())
       except <SSL cert verify error>:
           if _fetch_corporate_ca_chain(url):
               make_ssl_verify.cache_clear()
               return do_request(make_ssl_verify())   # 重试一次
           raise
   ```
   - `do_request(verify)` 是调用方传入的闭包，执行带该 `verify` 值的请求
   - 通用于 post / get
   - 判定 "cert verify error"：检查 httpx `ConnectError` 包裹的 `ssl.SSLCertVerificationError`

### 接入点（共享层，一次实现全局获益）

| 调用点 | 处理 |
|--------|------|
| `_fetch_available_models`（用户测试配置的路径）| 包 `with_ssl_retry` ← **主触发点** |
| `/{name}/ping` 端点 | 包 `with_ssl_retry` |
| `transport_httpx.post`（真实对话非流式）| 包 `with_ssl_retry` |
| `transport_httpx.stream_post`（流式）| 不包；依赖已缓存的 CA（测试/非流式请求先触发过） |
| MCP HTTP provider / skill-pull / skill-store | 不改；下次 `make_ssl_verify()` 重建时从 AppData 缓存自动获益 |

**流式不包重试的理由**：用户配置测试或第一条非流式请求一定先触发抓取并缓存 CA，等到流式对话时 CA 已就位。错误在迭代中途暴露使流式重试实现复杂，YAGNI。

## 依赖

- `cryptography`（解析 AIA 扩展）— 已确认在 uv 环境可用
- `urllib.request.getproxies()` — 读 Windows 注册表代理
- `socket` + HTTP CONNECT — 代理隧道
- Python 3.10+ `SSLSocket.get_unverified_chain()`（项目 Python 3.11–3.12，满足）

## 不纳入（YAGNI / 场景窄）

- **AD LDAP 查询**企业 CA 发布容器 — 需域连接 + LDAP 代码，重且只对域内有效，Schannel 已替原生应用做了
- **MDM/Intune、SCEP/NDES** — 最终落进 Windows 证书库，已被注册表 Phase 1 覆盖
- **Windows CTL autoupdate** — 管公网根，与企业私有 CA 无关
- **流式请求重试** — 见上

## 测试（pytest）

- `_fetch_corporate_ca_chain`：用样本证书测 AIA 扩展解析与链补全（网络部分 mock）
- `get_unverified_chain` 路径：mock 一个多证书链，验证中间证书被正确提取
- AppData 缓存读写：tmp 目录，验证指纹去重
- `http_ca_bundle` 多条目解析：文件 / URL / 混用三种
- 环境变量 CA 加载：设置临时 `SSL_CERT_FILE` 指向样本证书，验证被加载

## 受影响文件

- `app/common/ssl_verify.py` — 重写第 3 级
- `app/config/settings.py` — `http_ca_bundle` 语义升级为多条目（无需改字段类型，解析在 ssl_verify 内做）
- `app/llm/transport_httpx.py` — `post` 包 `with_ssl_retry`
- `app/api/v1/routes/llms.py` — `_fetch_available_models` + ping 包 `with_ssl_retry`
- `app/tools/mcp_http_provider.py`、`app/domain/services/skill_pull_service.py`、`app/skills/skill_store_client.py` — 不改，靠缓存获益
- 新增 pytest 测试文件
