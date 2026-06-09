  Tool 执行机制                                                                                                                    
   
  所有 tool 调用统一经过 ToolGateway 处理，执行流程分 5 步：                                                                       
                                                            
  LLM 输出 tool_call                                                                                                               
      ↓                                                     
  PolicyEngine.authorize()   ← 鉴权
      ↓
  写 RUNNING 审计记录
      ↓
  ToolRegistry.get(name).handler(args, ctx)  ← 实际执行
      ↓
  写 SUCCEEDED/FAILED 审计记录，返回 ToolResult

  ToolResult 结构统一：content、is_error、error_code、metadata（含 exit_code、http_status 等）。

  ---
  Tool 分类（三大来源）

  ┌─────────┬───────┬─────────────────────────────┬──────────────────────────────────────────────────────────────────┐
  │  类型   │ 数量  │          执行方式           │                             代表工具                             │
  ├─────────┼───────┼─────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ Builtin │ 12 个 │ 进程内 Python 函数          │ bash_exec, http_request, read/write/glob, exec_skill_script      │
  ├─────────┼───────┼─────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ MCP     │ 动态  │ 远程 MCP 协议（stdio/HTTP） │ @mcp/server-filesystem 等外部 MCP server                         │
  ├─────────┼───────┼─────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ Control │ 8 个  │ 内部状态变更                │ ask_human, submit_plan, submit_task_assessment, replan │
  └─────────┴───────┴─────────────────────────────┴──────────────────────────────────────────────────────────────────┘

  Control Tools 是特殊的内部工具，直接操作 task/session 状态，不走普通鉴权流程。

  ---
  Tool 分级（两个执行阶段）

  Agent 运行时分为 Actor（执行） 和 Observer（评估） 两个阶段，各自有独立的 tool 授权列表：

  ┌──────────┬─────────────────────────────────────────┬───────────────────────────────────────────────────────┐
  │   阶段   │                权限字段                 │                       典型用途                        │
  ├──────────┼─────────────────────────────────────────┼───────────────────────────────────────────────────────┤
  │ Actor    │ act_tool_list + mcp_act_servers         │ 有副作用操作：bash、写文件、HTTP 请求                 │
  ├──────────┼─────────────────────────────────────────┼───────────────────────────────────────────────────────┤
  │ Observer │ observe_tool_list + mcp_observe_servers │ 只读评估：submit_task_assessment、ask_human │
  └──────────┴─────────────────────────────────────────┴───────────────────────────────────────────────────────┘

  LLM 只能调用当前阶段被注入到 prompt 中的 tools，无法访问未授权的工具。

  ---
  鉴权机制（PolicyEngine）

  两层校验：

  1. 工具存在性：ToolRegistry.is_registered(tool_name)，否则抛 TOOL_NOT_FOUND
  2. Agent 权限：
    - 显式列表：工具名在 act_tool_list 或 observe_tool_list 中
    - MCP 组权限：工具属于 mcp_act_servers/mcp_observe_servers 订阅的某个 server（授权该 server 的全部工具）
    - 否则抛 TOOL_NOT_AUTHORIZED

  特殊规则：
  - Observer 内部调用（agent=None）：跳过鉴权
  - Control Tools：注册为 control 类型后，不受普通鉴权约束

  内置安全限制（不依赖鉴权，在 handler 层强制）：
  - bash_exec：黑名单过滤（rm -rf、sudo、curl|bash 等）+ 超时
  - http_request：SSRF 防护（正则拦截 localhost/10.x/192.168.x 等内网地址）+ Header 脱敏审计
  - read/write/glob：路径解析限制在 working_dir
  - exec_skill_script：必须绑定 skill，路径限制在 scripts/ 或 references/ 目录