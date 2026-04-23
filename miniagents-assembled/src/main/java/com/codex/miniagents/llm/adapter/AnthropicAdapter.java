package com.codex.miniagents.llm.adapter;

import com.codex.miniagents.llm.model.LlmContentBlock;
import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.llm.model.LlmRequest;
import com.codex.miniagents.llm.model.LlmResponse;
import com.codex.miniagents.llm.model.LlmTool;
import com.codex.miniagents.llm.model.LlmUsage;
import com.codex.miniagents.llm.model.ParsedResponse;
import com.codex.miniagents.llm.model.StreamChunk;
import com.codex.miniagents.llm.model.TextBlock;
import com.codex.miniagents.llm.model.ToolCallBlock;
import com.codex.miniagents.llm.transport.StreamTransport;
import com.codex.miniagents.llm.transport.Transport;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Queue;

public class AnthropicAdapter extends LlmAdapter {
    private final String apiKey;

    private final String baseUrl;

    private final Transport transport;

    private final int timeoutSec;

    private final ObjectMapper objectMapper = new ObjectMapper();

    public AnthropicAdapter(String apiKey, String baseUrl, Transport transport, int timeoutSec) {
        this.apiKey = apiKey;
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.transport = transport;
        this.timeoutSec = timeoutSec;
    }

    @Override
    public LlmResponse complete(LlmRequest req) {
        Map<String, Object> payload = buildPayload(req, false);
        String url = baseUrl + "/v1/messages";
        Map<String, Object> resp = transport.post(url, headers(), payload, timeoutSec);

        return LlmResponse.builder()
            .text(extractAnthropicText(resp))
            .raw(resp)
            .usage(extractAnthropicUsage(resp))
            .build();
    }

    @Override
    public ParsedResponse parseResponse(LlmResponse response) {
        Map<String, Object> raw = response.getRaw() == null ? Map.of() : response.getRaw();
        List<LlmContentBlock> blocks = new ArrayList<>();
        List<ToolCallBlock> toolCalls = new ArrayList<>();
        List<String> textParts = new ArrayList<>();

        for (Map<String, Object> block : getList(raw.get("content"))) {
            String blockType = asString(block.get("type"));
            if ("text".equals(blockType)) {
                String text = asString(block.get("text"));
                if (!text.isEmpty()) {
                    textParts.add(text);
                    blocks.add(new TextBlock("text", text));
                }
            } else if ("tool_use".equals(blockType)) {
                ToolCallBlock toolBlock = new ToolCallBlock("tool_call", asString(block.get("id")),
                    asString(block.get("name")), getMap(block.get("input")), blockType, block);
                blocks.add(toolBlock);
                toolCalls.add(toolBlock);
            }
        }

        String text = String.join("\n", textParts).trim();

        return ParsedResponse.builder()
            .text(text)
            .blocks(blocks)
            .toolCalls(toolCalls)
            .raw(raw)
            .usage(response.getUsage())
            .build();
    }

    @Override
    public Iterator<StreamChunk> stream(LlmRequest req) {
        if (!(transport instanceof StreamTransport streamTransport)) {
            return super.stream(req);
        }

        Map<String, Object> payload = buildPayload(req, true);
        String url = baseUrl + "/v1/messages";
        Map<Integer, Map<String, Object>> toolBlocks = new HashMap<>();
        Iterator<String> rawIterator = streamTransport.streamPost(url, headers(), payload, timeoutSec);

        return new Iterator<>() {
            private final Queue<StreamChunk> queue = new ArrayDeque<>();

            private boolean done = false;

            private int currentBlockIndex = -1;

            @Override
            public boolean hasNext() {
                fillQueue();
                return !queue.isEmpty();
            }

            @Override
            public StreamChunk next() {
                if (!hasNext()) {
                    throw new NoSuchElementException();
                }
                return queue.poll();
            }

            private void fillQueue() {
                while (queue.isEmpty() && !done && rawIterator.hasNext()) {
                    String rawLine = rawIterator.next();
                    Map<String, Object> event;
                    try {
                        event = objectMapper.readValue(rawLine, new TypeReference<>() {});
                    } catch (Exception e) {
                        continue;
                    }

                    String eventType = asString(event.get("type"));

                    if ("content_block_start".equals(eventType)) {
                        Map<String, Object> block = getMap(event.get("content_block"));
                        currentBlockIndex = getInt(event.get("index"), 0);
                        String currentBlockType = asString(block.get("type"));
                        if ("tool_use".equals(currentBlockType)) {
                            Map<String, Object> m = new LinkedHashMap<>();
                            m.put("id", asString(block.get("id")));
                            m.put("name", asString(block.get("name")));
                            m.put("arguments", "");
                            toolBlocks.put(currentBlockIndex, m);
                        }
                    } else if ("content_block_delta".equals(eventType)) {
                        Map<String, Object> delta = getMap(event.get("delta"));
                        String deltaType = asString(delta.get("type"));

                        if ("text_delta".equals(deltaType)) {
                            String text = asString(delta.get("text"));
                            if (!text.isEmpty()) {
                                queue.add(StreamChunk.builder().textDelta(text).build());
                            }
                        } else if ("input_json_delta".equals(deltaType)) {
                            String partial = asString(delta.get("partial_json"));
                            int idx = getInt(event.get("index"), currentBlockIndex);
                            if (toolBlocks.containsKey(idx)) {
                                Map<String, Object> buf = toolBlocks.get(idx);
                                buf.put("arguments", asString(buf.get("arguments")) + partial);

                                Map<String, Object> toolDelta = new LinkedHashMap<>();
                                toolDelta.put("index", idx);
                                toolDelta.putAll(buf);

                                queue.add(StreamChunk.builder().toolCallDelta(toolDelta).build());
                            }
                        }
                    } else if ("message_delta".equals(eventType)) {
                        Map<String, Object> usageData = getMap(event.get("usage"));
                        LlmUsage usage = usageData.isEmpty()
                            ? null
                            : LlmUsage.builder()
                                .completionTokens(getIntegerOrNull(usageData.get("output_tokens")))
                                .build();

                        queue.add(StreamChunk.builder().done(true).usage(usage).build());
                        done = true;
                    }
                }
            }
        };
    }

    private Map<String, String> headers() {
        return Map.of("x-api-key", apiKey, "anthropic-version", "2023-06-01", "Content-Type", "application/json");
    }

    private Map<String, Object> buildPayload(LlmRequest req, boolean stream) {
        SplitSystemResult split = splitSystemMessages(req);
        String systemText = split.systemText();
        List<LlmMessage> messages = split.messages();

        if (req.getSystemPrompt() != null && !req.getSystemPrompt().isBlank()) {
            systemText = (req.getSystemPrompt() + "\n" + systemText).trim();
        }

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("model", req.getModel());
        payload.put("messages", serializeMessagesAnthropic(messages));
        payload.put("stream", stream);

        if (!systemText.isBlank()) {
            payload.put("system", systemText);
        }
        if (req.getMaxTokens() == null) {
            throw new IllegalArgumentException("Anthropic 请求必须提供 max_tokens");
        }
        payload.put("max_tokens", req.getMaxTokens());

        if (req.getTemperature() != null) {
            payload.put("temperature", req.getTemperature());
        }
        if (req.getTopP() != null) {
            payload.put("top_p", req.getTopP());
        }
        if (req.getStop() != null) {
            payload.put("stop_sequences", req.getStop());
        }

        List<Map<String, Object>> allTools = new ArrayList<>();
        if (req.getTools() != null) {
            allTools.addAll(mapAnthropicTools(req.getTools()));
        }
        if (!allTools.isEmpty()) {
            payload.put("tools", allTools);
        }
        return payload;
    }

    private List<Map<String, Object>> serializeMessagesAnthropic(List<LlmMessage> messages) {
        List<Map<String, Object>> result = new ArrayList<>();
        int i = 0;
        while (i < messages.size()) {
            LlmMessage message = messages.get(i);
            if (message == null) {
                i += 1;
                continue;
            }
            if ("assistant".equals(message.getRole())) {
                List<Map<String, Object>> contentBlocks = new ArrayList<>();
                if (message.getContent() != null && !message.getContent().isBlank()) {
                    contentBlocks.add(Map.of("type", "text", "text", message.getContent()));
                }
                if (message.getToolCalls() != null) {
                    for (ToolCallBlock tc : message.getToolCalls()) {
                        Map<String, Object> block = new LinkedHashMap<>();
                        block.put("type", "tool_use");
                        block.put("id", tc.getId());
                        block.put("name", tc.getName());
                        block.put("input", tc.getInput() == null ? Map.of() : tc.getInput());
                        contentBlocks.add(block);
                    }
                }
                result.add(Map.of(
                    "role", "assistant",
                    "content", contentBlocks.isEmpty() ? message.getContent() : contentBlocks
                ));
                i += 1;
                continue;
            }
            if ("tool".equals(message.getRole())) {
                List<Map<String, Object>> toolResults = new ArrayList<>();
                List<String> fallbackTexts = new ArrayList<>();
                while (i < messages.size() && messages.get(i) != null && "tool".equals(messages.get(i).getRole())) {
                    LlmMessage toolMessage = messages.get(i);
                    if (toolMessage.getToolCallId() != null && !toolMessage.getToolCallId().isBlank()) {
                        Map<String, Object> block = new LinkedHashMap<>();
                        block.put("type", "tool_result");
                        block.put("tool_use_id", toolMessage.getToolCallId());
                        block.put("content", toolMessage.getContent() == null ? "" : toolMessage.getContent());
                        toolResults.add(block);
                    } else if (toolMessage.getContent() != null && !toolMessage.getContent().isBlank()) {
                        fallbackTexts.add(toolMessage.getContent());
                    }
                    i += 1;
                }
                if (!toolResults.isEmpty()) {
                    result.add(Map.of("role", "user", "content", toolResults));
                }
                if (!fallbackTexts.isEmpty()) {
                    result.add(Map.of("role", "user", "content", String.join("\n\n", fallbackTexts)));
                }
                continue;
            }
            result.add(Map.of(
                "role", message.getRole(),
                "content", message.getContent() == null ? "" : message.getContent()
            ));
            i += 1;
        }
        return result;
    }

    private String extractAnthropicText(Map<String, Object> resp) {
        List<Map<String, Object>> content = getList(resp.get("content"));
        if (content.isEmpty()) {
            return "";
        }
        return asString(content.get(0).get("text"));
    }

    private LlmUsage extractAnthropicUsage(Map<String, Object> resp) {
        Map<String, Object> usage = getMap(resp.get("usage"));
        if (usage.isEmpty()) {
            return null;
        }
        Integer inputTokens = getIntegerOrNull(usage.get("input_tokens"));
        Integer outputTokens = getIntegerOrNull(usage.get("output_tokens"));
        Integer total = ((inputTokens == null ? 0 : inputTokens) + (outputTokens == null ? 0 : outputTokens));
        if (total == 0) {
            total = null;
        }
        return LlmUsage.builder().promptTokens(inputTokens).completionTokens(outputTokens).totalTokens(total).build();
    }

    private SplitSystemResult splitSystemMessages(LlmRequest req) {
        List<String> systemParts = new ArrayList<>();
        List<LlmMessage> nonSystem = new ArrayList<>();
        for (LlmMessage m : req.getMessages()) {
            if ("system".equals(m.getRole())) {
                systemParts.add(m.getContent());
            } else {
                nonSystem.add(m);
            }
        }
        return new SplitSystemResult(String.join("\n", systemParts).trim(), nonSystem);
    }

    private List<Map<String, Object>> mapAnthropicTools(List<LlmTool> tools) {
        List<Map<String, Object>> mapped = new ArrayList<>();
        for (LlmTool tool : tools) {
            if (!"function".equals(tool.getType())) {
                throw new IllegalArgumentException("_map_anthropic_tools 仅处理 function 工具，当前: " + tool.getType());
            }

            Map<String, Object> item = new LinkedHashMap<>();
            item.put("type", "custom");
            item.put("name", tool.getName());
            item.put("input_schema", tool.getInputSchema().toDict());
            if (tool.getDescription() != null && !tool.getDescription().isBlank()) {
                item.put("description", tool.getDescription());
            }
            mapped.add(item);
        }
        return mapped;
    }

    private record SplitSystemResult(String systemText, List<LlmMessage> messages) {}

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> getList(Object obj) {
        return obj instanceof List<?> list ? (List<Map<String, Object>>) list : List.of();
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> getMap(Object obj) {
        return obj instanceof Map<?, ?> map ? (Map<String, Object>) map : new LinkedHashMap<>();
    }

    private String asString(Object obj) {
        return obj == null ? "" : String.valueOf(obj);
    }

    private int getInt(Object obj, int defaultVal) {
        if (obj instanceof Number n) {
            return n.intValue();
        }
        try {
            return obj == null ? defaultVal : Integer.parseInt(String.valueOf(obj));
        } catch (Exception e) {
            return defaultVal;
        }
    }

    private Integer getIntegerOrNull(Object obj) {
        if (obj == null) {
            return null;
        }
        if (obj instanceof Number n) {
            return n.intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(obj));
        } catch (Exception e) {
            return null;
        }
    }
}
