# LLM Client 抽象层重构设计

## 1. 背景

当前 `app/llm/client.py` 中同时存在：

- `BaseChatClient`：项目内部抽象接口
- `LLMClient`：基于 Agent Framework 的通用适配实现

在现状下，`LLMClient` 承担了两类职责：

1. 统一 miniAgents 的同步调用接口
2. 解析 Agent Framework 返回的 `ChatResponse.messages[].contents[]`

这让 `LLMClient` 既是抽象层，又是默认实现层。进一步的问题是：

- `app/llm/factory.py` 中的 `AnthropicChatClient` 和 `OpenAIChatClient` 只负责构造底层 AF client，自身没有 provider-specific parsing 责任
- 如果后续需要支持更多 `Content.type`，或不同 provider 的特殊内容块，解析逻辑会继续膨胀在一个通用类里
- 从职责划分上看，当前 `LLMClient` 更像 “AF 通用实现”，而不是 “项目的 LLM 抽象接口”

这次重构的目标是：

- 合并 `BaseChatClient` 和 `LLMClient`
- 将 `LLMClient` 变为唯一的抽象基类
- 将具体 provider 的 AF client 初始化、响应解析、流式解析，下沉到 `AnthropicChatClient` / `OpenAIChatClient`

## 2. 设计目标

### 2.1 核心目标

- 用一个抽象类 `LLMClient` 替代当前 `BaseChatClient + LLMClient` 双层结构
- `LLMClient` 只声明项目内部统一接口与可复用的公共辅助方法
- `AnthropicChatClient`、`OpenAIChatClient` 分别负责：
  - 构造底层 AF client
  - 调用 AF `get_response()`
  - 将 AF `ChatResponse` 转成项目自己的 `LLMResponse`
  - 处理 provider-specific 内容解析差异

### 2.2 非目标

- 本次不改动 `Planner / Actor / Observer` 对 `LLMResponse` 的消费方式
- 本次不重做 streaming API 设计，只在现有 `StreamChunk` 模型下重构实现归属
- 本次不移除 `LLMMessage.to_af()`、`LLMTool.to_af()`，除非实现中顺手发现收益明显

## 3. 对 Agent Framework 的代码观察

本设计基于当前虚拟环境中的 AF 实现，重点结论如下。

### 3.1 AF 已将 provider 原始响应归一成统一的 `ChatResponse`

在以下实现中：

- `.venv/Lib/site-packages/agent_framework/openai/_chat_client.py`
- `.venv/Lib/site-packages/agent_framework_anthropic/_chat_client.py`

两个 provider 最终都会返回 AF 的 `ChatResponse`，核心结构为：

- `ChatResponse.messages: list[Message]`
- `Message.contents: list[Content]`
- `ChatResponse.usage_details`
- `ChatResponse.finish_reason`
- `ChatResponse.text`

这意味着 miniAgents 不需要直接解析 OpenAI/Anthropic SDK 的原始对象，而应该基于 AF 统一抽象进行二次转换。

### 3.2 `ChatResponse.text` 只是文本拼接，不是完整语义视图

AF 的 `ChatResponse.text` 和 `Message.text` 只会拼接 `Content.type == "text"` 的内容。

因此以下信息不会完整体现在 `response.text` 中：

- `function_call`
- `text_reasoning`
- `mcp_server_tool_call`
- `code_interpreter_tool_call`
- `shell_tool_call`

结论：

- miniAgents 的解析入口必须以 `response.messages[].contents[]` 为准
- `response.text` 只能作为文本兜底，不适合作为主解析依据

### 3.3 OpenAI 与 Anthropic 在 AF 层仍然保留 provider-specific content 特征

从 AF 实现看：

- OpenAI 会产生 `Content.from_function_call(...)`
- OpenAI 会通过 `Content.from_text_reasoning(protected_data=...)` 注入 reasoning 细节
- Anthropic 除 `function_call` 外，还支持：
  - `mcp_server_tool_call`
  - `code_interpreter_tool_call`
  - `shell_tool_call`
  - `text_reasoning`

结论：

- 虽然 AF 做了统一抽象，但不同 provider 在“会出现哪些 content type”上仍然不同
- 因此“解析责任放到具体 provider client”是合理的

### 3.4 AF 没有提供我们当前需要的 `tool_calls` 便捷属性

AF 的 `ChatResponse` 暴露的是：

- `messages`
- `text`
- `usage_details`
- `finish_reason`

而 miniAgents 当前业务层直接依赖的是：

- `LLMResponse.tool_calls`
- `LLMResponse.blocks`
- `LLMResponse.text`

因此我们仍然需要自己的二次抽取层。

## 4. 当前问题

### 4.1 抽象层命名与职责不一致

当前：

- `BaseChatClient` 才是抽象类
- `LLMClient` 是 AF 默认实现

但从项目语义上看，`LLMClient` 其实更适合作为“项目统一 LLM 接口”的名字。

### 4.2 provider-specific parsing 未真正下沉

当前的 `AnthropicChatClient` / `OpenAIChatClient` 只做构造，不做解析。

这意味着：

- provider 特有内容类型无法自然扩展
- 通用适配器需要知道越来越多 provider 细节

### 4.3 通用类过度依赖单一解析路径

当前 `app/llm/client.py` 中的 `_parse_af_content()` 假定只关心：

- `text`
- `function_call`

这对现阶段业务够用，但跟 AF 当前能力相比已经偏保守。

## 5. 重构后的目标架构

### 5.1 类关系

重构后建议结构：

```python
class LLMClient(ABC):
    @abstractmethod
    def send_message(...) -> LLMResponse: ...

    @abstractmethod
    def stream_message(...) -> Iterator[StreamChunk]: ...

    @abstractmethod
    def _create_af_client(self) -> Any: ...

    @abstractmethod
    def _build_response_from_af(self, response: Any) -> LLMResponse: ...
```

```python
class AnthropicChatClient(LLMClient):
    def __init__(...): ...
    def _create_af_client(self) -> Any: ...
    def send_message(...) -> LLMResponse: ...
    def stream_message(...) -> Iterator[StreamChunk]: ...
    def _build_response_from_af(...) -> LLMResponse: ...
    def _parse_anthropic_content(...) -> LLMContentBlock | None: ...
```

```python
class OpenAIChatClient(LLMClient):
    def __init__(...): ...
    def _create_af_client(self) -> Any: ...
    def send_message(...) -> LLMResponse: ...
    def stream_message(...) -> Iterator[StreamChunk]: ...
    def _build_response_from_af(...) -> LLMResponse: ...
    def _parse_openai_content(...) -> LLMContentBlock | None: ...
```

### 5.2 职责边界

`LLMClient` 负责：

- 对外暴露统一接口名
- 保存 provider 无关的公共辅助逻辑
- 提供请求参数构造等共享方法
- 提供通用解析辅助函数，如 JSON 参数解析

`AnthropicChatClient` / `OpenAIChatClient` 负责：

- 初始化底层 AF client
- 调用 AF `get_response()`
- 解析 `ChatResponse.messages[].contents[]`
- 输出最终 `LLMResponse`

## 6. 推荐的实现方式

### 6.1 `LLMClient` 作为抽象类保留的内容

建议 `app/llm/client.py` 中只保留：

- `LLMClient(ABC)`
- `_build_af_options(...)`
- `_parse_tool_arguments(...)`
- 可选的 provider-agnostic 遍历工具，例如 `_iter_contents(response)`

建议接口草案：

```python
class LLMClient(ABC):
    def __init__(self) -> None:
        self._af_client = self._create_af_client()

    @abstractmethod
    def _create_af_client(self) -> Any:
        ...

    @abstractmethod
    def send_message(...) -> LLMResponse:
        ...

    @abstractmethod
    def stream_message(...) -> Iterator[StreamChunk]:
        ...

    def _build_text_response(self, text: str, *, usage: LLMUsage | None = None) -> LLMResponse:
        ...

    def _parse_tool_arguments(self, arguments: Any) -> dict[str, Any]:
        ...
```

说明：

- `send_message()` 保持抽象，而不是在基类中直接实现
- 这样可以强制每个 provider 显式定义自己的解析路径
- 共享逻辑只保留真正 provider 无关的部分

### 6.2 provider 子类中的重复逻辑允许有限存在

`AnthropicChatClient` 和 `OpenAIChatClient` 的 `send_message()` 很可能长得很像：

1. `messages -> AF Message`
2. `_build_af_options(...)`
3. `self._af_client.get_response(...)`
4. `_build_response_from_af(...)`

这类少量重复是可以接受的。原因是：

- 这次重构的重点是职责清晰，不是极限去重
- 一旦 provider 在解析路径上分叉，硬塞回基类反而更绕

如果后面确认二者逻辑高度一致，再考虑引入模板方法：

```python
def send_message(...):
    af_messages = self._prepare_af_messages(messages)
    options = self._prepare_af_options(...)
    response = self._invoke_af(...)
    return self._build_response_from_af(response)
```

但不建议在本次第一版就做得过抽象。

## 7. `LLMResponse` 建议保持的结构

为了不影响运行时层，建议保留当前 `LLMResponse` 结构：

```python
@dataclass
class LLMResponse:
    text: str
    reasoning: str | None = None
    blocks: list[LLMContentBlock] = field(default_factory=list)
    tool_calls: list[ToolCallBlock] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    usage: LLMUsage | None = None
    native_response: Any | None = None
```

说明：

- `text`：最终文本输出
- `reasoning`：归一后的 reasoning 摘要或序列化内容
- `blocks`：完整的已解析内容块
- `tool_calls`：业务层最常消费的工具调用列表
- `raw`：可序列化调试信息
- `native_response`：原始 AF `ChatResponse`

## 8. provider-specific 解析策略

### 8.1 OpenAIChatClient

建议优先支持：

- `text` -> `TextBlock`
- `function_call` -> `ToolCallBlock`
- `text_reasoning` -> 可先累计到 `reasoning`

第一版策略：

- `text_reasoning` 不一定要单独定义新 block 类型
- 可以先累计到 `reasoning` 字段
- `blocks` 中可暂时只保留 `TextBlock` / `ToolCallBlock`

原因：

- 当前业务层主要关心 `text` 和 `tool_calls`
- OpenAI reasoning 在 AF 中常见为 `protected_data`，直接暴露原始结构即可

### 8.2 AnthropicChatClient

建议第一版同样至少支持：

- `text`
- `function_call`
- `text_reasoning`

对于 Anthropic 特有内容：

- `mcp_server_tool_call`
- `code_interpreter_tool_call`
- `shell_tool_call`

有两种策略：

1. 保守策略
   先忽略这些 block，只把它们记录到 `raw`
2. 扩展策略
   后续新增 block 类型，例如：
   - `HostedToolCallBlock`
   - `ShellToolCallBlock`
   - `CodeInterpreterToolCallBlock`

本次建议采用保守策略，原因是当前 miniAgents runtime 只消费通用 function tool call。

## 9. 文件级改造方案

### 9.1 `app/llm/client.py`

改造为：

- 删除 `BaseChatClient`
- 保留并重命名语义后的 `LLMClient(ABC)`
- 只放抽象接口和共享 helper

### 9.2 `app/llm/factory.py`

让这里的 provider 类成为真正实现类：

- `class AnthropicChatClient(LLMClient)`
- `class OpenAIChatClient(LLMClient)`

各自负责：

- `_create_af_client()`
- `send_message()`
- `stream_message()`
- `_build_response_from_af()`
- provider-specific content parsing

### 9.3 `app/llm/mock_client.py`

`MockChatClient` 改为：

```python
class MockChatClient(LLMClient):
    ...
```

或者如果不想让 mock 依赖 AF 初始化路径，则可以：

- 让 `MockChatClient` 直接继承新的 `LLMClient`
- `_create_af_client()` 返回 `None`
- `send_message()` 直接返回构造好的 `LLMResponse`

这点需要在实现时稍微留意基类 `__init__` 设计，避免把 mock 搞复杂。

## 10. 迁移步骤

### 阶段 1：抽象层整合

- 删除 `BaseChatClient`
- 将 `LLMClient` 改为抽象类
- 调整 `factory.py` 中 provider 类继承关系

### 阶段 2：下沉解析逻辑

- 将当前 `app/llm/client.py` 中 `_build_response_from_af()`、`_parse_af_response()`、`_parse_af_content()` 分别迁移到：
  - `OpenAIChatClient`
  - `AnthropicChatClient`

### 阶段 3：保留共享 helper

- `json arguments -> dict` 的解析逻辑保留在抽象基类
- `_build_af_options(...)` 保留在基类或模块级 helper

### 阶段 4：验证调用方

确认以下模块不需要额外改接口：

- `app/runtime/planner.py`
- `app/runtime/actor.py`
- `app/runtime/observer.py`
- `app/runtime/agent_loop.py`
- `app/llm/registry.py`
- `app/llm/mock_client.py`

## 11. 风险与注意点

### 11.1 不要把“AF 已统一”误解成“不需要 provider 子类解析”

AF 的确统一了基础结构，但不同 provider 会产生不同 `Content.type` 组合。
因此 provider-specific parsing 仍然有意义。

### 11.2 避免基类再次膨胀

如果把太多默认解析逻辑放回 `LLMClient`，会重新回到“抽象类其实是默认实现”的老问题。

建议原则：

- 基类只放 helper
- provider 子类放完整解析入口

### 11.3 mock client 的继承设计要提前想清楚

如果新基类 `__init__()` 强制创建 `_af_client`，mock 可能会被迫实现空 AF client。

建议做法：

- `LLMClient.__init__()` 不主动创建 client
- 而是由具体子类自行设置 `self._af_client`

这样更灵活，也更利于测试。

## 12. 推荐的最终方案

推荐采用以下版本：

1. 删除 `BaseChatClient`
2. `LLMClient` 成为唯一抽象基类
3. `LLMClient` 不负责默认解析实现，只负责接口声明和共享 helper
4. `AnthropicChatClient` / `OpenAIChatClient` 自己实现 `send_message()` 和 `_build_response_from_af()`
5. 当前 `LLMResponse` 结构保持不变，避免 runtime 层再次迁移
6. 第一版只正式支持 `text` / `function_call` / `text_reasoning`
7. Anthropic 的 hosted tool call 类型先保留在 `raw`，不急着映射成新 block

## 13. 后续可选增强

如果这次重构完成后运行稳定，下一步可以继续做：

- 将 `LLMMessage.to_af()` / `LLMTool.to_af()` 也迁移到 provider client 层
- 为 `text_reasoning` 增加专门的 block 类型
- 为 Anthropic hosted tools 增加专门 block 类型
- 将 `stream_message()` 也升级为支持 tool-call delta / reasoning delta

## 14. 结论

这次重构的本质，不是简单把代码从一个文件挪到另一个文件，而是重新明确抽象边界：

- `LLMClient` 代表 miniAgents 内部统一的 LLM 能力接口
- provider 子类负责把 Agent Framework 的统一响应，转换成 miniAgents 所需的业务响应

在 AF 已提供统一 `ChatResponse / Message / Content` 模型的前提下，这样的分层会比当前结构更清晰，也更适合后续继续支持 provider-specific 能力扩展。
