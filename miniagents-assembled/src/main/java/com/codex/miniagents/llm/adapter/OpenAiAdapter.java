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
import java.util.Objects;
import java.util.Queue;

public class OpenAiAdapter extends LlmAdapter {
    private final String apiKey;

    private final String baseUrl;

    private final Transport transport;

    private final int timeoutSec;

    private final ObjectMapper objectMapper = new ObjectMapper();

    public OpenAiAdapter(String apiKey, String baseUrl, Transport transport, int timeoutSec) {
        this.apiKey = apiKey;
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.transport = transport;
        this.timeoutSec = timeoutSec;
    }

    @Override
    public LlmResponse complete(LlmRequest req) {
        Map<String, Object> payload = buildPayload(req, false);
        String url = baseUrl + "/v1/chat/completions";
        Map<String, Object> resp = transport.post(url, headers(), payload, timeoutSec);

        return LlmResponse.builder().text(extractOpenAiText(resp)).raw(resp).usage(extractOpenAiUsage(resp)).build();
    }

    @Override
    public ParsedResponse parseResponse(LlmResponse response) {
        Map<String, Object> raw = response.getRaw() == null ? Map.of() : response.getRaw();
        Map<String, Object> message = extractOpenAiMessage(raw);

        List<LlmContentBlock> blocks = new ArrayList<>();
        List<ToolCallBlock> toolCalls = new ArrayList<>();

        for (String text : extractOpenAiTextParts(message)) {
            blocks.add(new TextBlock("text", text));
        }

        for (ToolCallBlock call : extractOpenAiToolCalls(message)) {
            blocks.add(call);
            toolCalls.add(call);
        }

        String text = blocks.stream()
            .filter(TextBlock.class::isInstance)
            .map(TextBlock.class::cast)
            .map(TextBlock::getText)
            .filter(Objects::nonNull)
            .reduce((a, b) -> a + "\n" + b)
            .orElse("")
            .trim();

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
        String url = baseUrl + "/v1/chat/completions";

        Map<Integer, Map<String, Object>> toolCallBuffers = new HashMap<>();
        Iterator<String> rawIterator = streamTransport.streamPost(url, headers(), payload, timeoutSec);

        return new Iterator<>() {
            private final Queue<StreamChunk> queue = new ArrayDeque<>();

            private boolean done = false;

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
                    Map<String, Object> data;
                    try {
                        data = objectMapper.readValue(rawLine, new TypeReference<>() {});
                    } catch (Exception e) {
                        continue;
                    }

                    List<Map<String, Object>> choices = getList(data.get("choices"));
                    if (choices.isEmpty()) {
                        Object usageData = data.get("usage");
                        if (usageData instanceof Map<?, ?> usageMap) {
                            queue.add(StreamChunk.builder().done(true).usage(parseUsage(castMap(usageMap))).build());
                            done = true;
                        }
                        continue;
                    }

                    Map<String, Object> choice = choices.get(0);
                    Map<String, Object> delta = getMap(choice.get("delta"));
                    Object finishReason = choice.get("finish_reason");

                    String textDelta = asString(delta.get("content"));
                    if (!textDelta.isEmpty()) {
                        queue.add(StreamChunk.builder().textDelta(textDelta).build());
                    }

                    for (Map<String, Object> tcDelta : getList(delta.get("tool_calls"))) {
                        int idx = getInt(tcDelta.get("index"), 0);
                        Map<String, Object> buf = toolCallBuffers.computeIfAbsent(idx, k -> {
                            Map<String, Object> m = new LinkedHashMap<>();
                            m.put("id", "");
                            m.put("name", "");
                            m.put("arguments", "");
                            return m;
                        });

                        String id = asString(tcDelta.get("id"));
                        if (!id.isEmpty()) {
                            buf.put("id", id);
                        }

                        Map<String, Object> fn = getMap(tcDelta.get("function"));
                        String name = asString(fn.get("name"));
                        if (!name.isEmpty()) {
                            buf.put("name", asString(buf.get("name")) + name);
                        }

                        String arguments = asString(fn.get("arguments"));
                        if (!arguments.isEmpty()) {
                            buf.put("arguments", asString(buf.get("arguments")) + arguments);
                        }

                        Map<String, Object> deltaObj = new LinkedHashMap<>();
                        deltaObj.put("index", idx);
                        deltaObj.putAll(buf);
                        queue.add(StreamChunk.builder().toolCallDelta(deltaObj).build());
                    }

                    if (finishReason != null) {
                        Map<String, Object> usageData = getMap(data.get("usage"));
                        queue.add(StreamChunk.builder()
                            .done(true)
                            .usage(usageData.isEmpty() ? null : parseUsage(usageData))
                            .build());
                        done = true;
                    }
                }
            }
        };
    }

    private Map<String, String> headers() {
        return Map.of("Authorization", "Bearer " + apiKey, "Content-Type", "application/json");
    }

    private Map<String, Object> buildPayload(LlmRequest req, boolean stream) {
        List<LlmMessage> messages = mergeSystemPrompt(req);

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("model", req.getModel());
        payload.put("messages", serializeMessagesOpenAi(messages));
        payload.put("stream", stream);
        if (req.getTools() != null && !req.getTools().isEmpty()) {
            payload.put("tools", mapOpenAiTools(req.getTools()));
        }
        if (req.getTemperature() != null) {
            payload.put("temperature", req.getTemperature());
        }
        if (req.getMaxTokens() != null) {
            payload.put("max_tokens", req.getMaxTokens());
        }
        if (req.getTopP() != null) {
            payload.put("top_p", req.getTopP());
        }
        if (req.getStop() != null) {
            payload.put("stop", req.getStop());
        }
        return payload;
    }

    private List<Map<String, Object>> serializeMessagesOpenAi(List<LlmMessage> messages) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (LlmMessage message : messages) {
            if (message == null) {
                continue;
            }
            if ("assistant".equals(message.getRole()) && message.getToolCalls() != null && !message.getToolCalls().isEmpty()) {
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("role", "assistant");
                if (message.getContent() != null && !message.getContent().isBlank()) {
                    item.put("content", message.getContent());
                }
                List<Map<String, Object>> toolCalls = new ArrayList<>();
                for (ToolCallBlock tc : message.getToolCalls()) {
                    Map<String, Object> function = new LinkedHashMap<>();
                    function.put("name", tc.getName());
                    try {
                        function.put("arguments", objectMapper.writeValueAsString(tc.getInput() == null ? Map.of() : tc.getInput()));
                    } catch (Exception e) {
                        function.put("arguments", "{}");
                    }
                    Map<String, Object> call = new LinkedHashMap<>();
                    call.put("id", tc.getId());
                    call.put("type", "function");
                    call.put("function", function);
                    toolCalls.add(call);
                }
                item.put("tool_calls", toolCalls);
                result.add(item);
                continue;
            }
            if ("tool".equals(message.getRole())) {
                if (message.getToolCallId() != null && !message.getToolCallId().isBlank()) {
                    Map<String, Object> item = new LinkedHashMap<>();
                    item.put("role", "tool");
                    item.put("tool_call_id", message.getToolCallId());
                    item.put("content", message.getContent() == null ? "" : message.getContent());
                    result.add(item);
                } else {
                    result.add(Map.of(
                        "role", "user",
                        "content", message.getContent() == null ? "" : message.getContent()
                    ));
                }
                continue;
            }
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("role", message.getRole());
            item.put("content", message.getContent());
            result.add(item);
        }
        return result;
    }

    private LlmUsage parseUsage(Map<String, Object> usage) {
        return LlmUsage.builder()
            .promptTokens(getIntegerOrNull(usage.get("prompt_tokens")))
            .completionTokens(getIntegerOrNull(usage.get("completion_tokens")))
            .totalTokens(getIntegerOrNull(usage.get("total_tokens")))
            .build();
    }

    private String extractOpenAiText(Map<String, Object> resp) {
        List<Map<String, Object>> choices = getList(resp.get("choices"));
        if (choices.isEmpty()) {
            return "";
        }
        Map<String, Object> message = getMap(choices.get(0).get("message"));
        return asString(message.get("content"));
    }

    private LlmUsage extractOpenAiUsage(Map<String, Object> resp) {
        Map<String, Object> usage = getMap(resp.get("usage"));
        return usage.isEmpty() ? null : parseUsage(usage);
    }

    private Map<String, Object> extractOpenAiMessage(Map<String, Object> resp) {
        List<Map<String, Object>> choices = getList(resp.get("choices"));
        if (choices.isEmpty()) {
            return Map.of();
        }
        return getMap(choices.get(0).get("message"));
    }

    private List<String> extractOpenAiTextParts(Map<String, Object> message) {
        Object content = message.get("content");
        if (content instanceof String s) {
            return s.isEmpty() ? List.of() : List.of(s);
        }
        if (content instanceof List<?> list) {
            List<String> result = new ArrayList<>();
            for (Object itemObj : list) {
                Map<String, Object> item = castMap(itemObj);
                if ("text".equals(item.get("type")) && item.get("text") != null) {
                    result.add(asString(item.get("text")));
                }
            }
            return result;
        }
        return List.of();
    }

    private List<ToolCallBlock> extractOpenAiToolCalls(Map<String, Object> message) {
        List<ToolCallBlock> blocks = new ArrayList<>();
        for (Map<String, Object> call : getList(message.get("tool_calls"))) {
            Map<String, Object> fn = getMap(call.get("function"));
            String argsRaw = asString(fn.get("arguments"));
            Map<String, Object> args;
            try {
                args = argsRaw.isEmpty()
                    ? new LinkedHashMap<>()
                    : objectMapper.readValue(argsRaw, new TypeReference<>() {});
            } catch (Exception e) {
                args = new LinkedHashMap<>();
                args.put("_raw_arguments", argsRaw);
            }

            blocks.add(new ToolCallBlock("tool_call", asString(call.get("id")), asString(fn.get("name")), args,
                asString(call.getOrDefault("type", "function")), call));
        }
        return blocks;
    }

    private List<LlmMessage> mergeSystemPrompt(LlmRequest req) {
        List<LlmMessage> messages = new ArrayList<>(req.getMessages());
        if (req.getSystemPrompt() == null || req.getSystemPrompt().isBlank()) {
            return messages;
        }

        for (int i = 0; i < messages.size(); i++) {
            LlmMessage m = messages.get(i);
            if ("system".equals(m.getRole())) {
                messages.set(i, LlmMessage.builder()
                    .role("system")
                    .content((req.getSystemPrompt() + "\n" + m.getContent()).trim())
                    .toolCallId(m.getToolCallId())
                    .toolCalls(m.getToolCalls())
                    .build());
                return messages;
            }
        }

        List<LlmMessage> result = new ArrayList<>();
        result.add(LlmMessage.builder().role("system").content(req.getSystemPrompt()).build());
        result.addAll(messages);
        return result;
    }

    private List<Map<String, Object>> mapOpenAiTools(List<LlmTool> tools) {
        List<Map<String, Object>> mapped = new ArrayList<>();
        for (LlmTool tool : tools) {
            if (!"function".equals(tool.getType())) {
                throw new IllegalArgumentException("OpenAI 仅支持 function 工具，当前: " + tool.getType());
            }
            Map<String, Object> fn = new LinkedHashMap<>();
            fn.put("name", tool.getName());
            fn.put("parameters", tool.getInputSchema().toDict());
            if (tool.getDescription() != null && !tool.getDescription().isBlank()) {
                fn.put("description", tool.getDescription());
            }

            Map<String, Object> item = new LinkedHashMap<>();
            item.put("type", "function");
            item.put("function", fn);
            mapped.add(item);
        }
        return mapped;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> castMap(Object obj) {
        return obj instanceof Map<?, ?> map ? (Map<String, Object>) map : new LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> getList(Object obj) {
        return obj instanceof List<?> list ? (List<Map<String, Object>>) list : List.of();
    }

    private Map<String, Object> getMap(Object obj) {
        return castMap(obj);
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
