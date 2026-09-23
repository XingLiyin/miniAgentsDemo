# 六张图 · 说明

全部从 `app/` 下的真实代码抽的：类名、方法签名、状态名、事件名都能在仓库里对上。
每张图有三个文件：`.mmd`（Mermaid 源码，可改）、`.png`（2 倍图，直接插 PPT）、`.svg`（矢量，PowerPoint 可插入并无损缩放，推荐）。

| 文件 | 类型 | 画的是什么 | 对应代码 |
|---|---|---|---|
| `1_seq_task_lifecycle` | 时序图 | 一个 task 从创建到完成的完整路径，含 SUSPENDED 分支和事件回流 | `task_manager.py` · `task_queue.py` · `agent_loop.py` |
| `2_seq_tool_call` | 时序图 | `ToolGateway.call()` 的六步：授权 → 审计 → 执行 → 审计 → SSE → 截断 | `tool_gateway.py` · `policy_rule.py` |
| `3_class_runtime` | 类图 | AgentLoop 与三个阶段对象，以及阶段间的三个 dataclass 契约 | `agent_loop.py` · `types.py` |
| `4_class_llm` | 类图 | LLM 适配层：Protocol 传输层、抽象适配器、三个实现、两级注册表 | `llm/base.py` · `provider_registry.py` |
| `5_class_tool` | 类图 | 工具侧：网关、策略引擎、规则继承体系、注册表 | `tool_gateway.py` · `policy_engine.py` · `tools/registry.py` |
| `6_state_task` | 状态图 | Task 状态机主干，含 reopen 与重试两条回边 | `domain/state_machine.py` |

## 建议怎么用

**插进现在这份 PPT**：

- `1_seq_task_lifecycle` → 放在 05 页（AgentLoop 执行链）之后，单独一页，讲"一个 task 的一生"时用
- `3_class_runtime` → 05 页右侧代码截图的替代或补充
- `4_class_llm` + `5_class_tool` → 06 页（五处扩展点）的展开，答辩被追问时翻出来
- `6_state_task` → 07 页"可靠"那一格的展开
- `2_seq_tool_call` → 07 页"安全"那一格的展开

**建议只把 1 号图放进正文**，其余四张放在 PPT 最后当附录页（放映时跳过，提问时跳转）。评委问到哪块翻哪张，比正文堆图有效。

**插入方法**：PowerPoint → 插入 → 图片 → 选 `.svg`。SVG 插进去可以右键"转换为形状"，之后能单独改某个框的颜色或文字。

## 改图

装了 mermaid-cli 之后：

```bash
mmdc -i 1_seq_task_lifecycle.mmd -o 1_seq_task_lifecycle.png -c config.json -b white -s 2
```

`config.json` 里是配色（深蓝 `#16202A` + 橙 `#C75B2C`），和 PPT 一致。
也可以直接把 `.mmd` 内容贴到 https://mermaid.live 在线改。

## 注意

`4_class_llm` 里 `HttpxTransport` 同时实现两个 Protocol，图上画的是虚线实现关系——**代码里它并没有显式继承任何基类**，是结构化类型匹配（`Protocol` + `runtime_checkable`）。讲的时候可以点一句，这是这层设计的关键。
