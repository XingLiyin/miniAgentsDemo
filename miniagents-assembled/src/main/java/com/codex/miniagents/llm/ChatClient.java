package com.codex.miniagents.llm;

import com.codex.miniagents.llm.adapter.LlmAdapter;
import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.llm.model.LlmRequest;
import com.codex.miniagents.llm.model.LlmResponse;
import com.codex.miniagents.llm.model.LlmTool;
import com.codex.miniagents.llm.model.ParsedResponse;
import com.codex.miniagents.llm.model.StreamChunk;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

public class ChatClient {
    private final LlmAdapter adapter;

    private final String model;

    private final int defaultMaxTokens;

    public ChatClient(LlmAdapter adapter, String model, int defaultMaxTokens) {
        this.adapter = adapter;
        this.model = model;
        this.defaultMaxTokens = defaultMaxTokens;
    }

    public LlmResponse sendMessage(LlmRequest request) {
        LlmRequest llmRequest = request.clone();
        llmRequest.setModel(model);
        if (llmRequest.getMaxTokens() == null) {
            llmRequest.setMaxTokens(defaultMaxTokens);
        }
        return adapter.complete(llmRequest);
    }

    public LlmResponse sendMessage(List<LlmMessage> messages, String systemPrompt, List<LlmTool> tools) {
        LlmRequest request = LlmRequest.builder()
            .model(model)
            .messages(messages == null ? List.of() : new ArrayList<>(messages))
            .systemPrompt(systemPrompt)
            .tools(tools == null ? List.of() : new ArrayList<>(tools))
            .maxTokens(defaultMaxTokens)
            .build();
        return adapter.complete(request);
    }

    public Iterator<StreamChunk> streamMessage(List<LlmMessage> messages, String systemPrompt, List<LlmTool> tools) {
        return streamMessage(messages, systemPrompt, tools, Map.of());
    }

    public Iterator<StreamChunk> streamMessage(List<LlmMessage> messages, String systemPrompt, List<LlmTool> tools,
        Map<String, Object> metadata) {
        LlmRequest request = LlmRequest.builder()
            .model(model)
            .messages(messages == null ? List.of() : new ArrayList<>(messages))
            .systemPrompt(systemPrompt)
            .tools(tools == null ? List.of() : new ArrayList<>(tools))
            .maxTokens(defaultMaxTokens)
            .metadata(metadata == null ? Map.of() : metadata)
            .build();
        return adapter.stream(request);
    }

    public ParsedResponse parseResponse(LlmResponse response) {
        return adapter.parseResponse(response);
    }

    public LlmResponse sendMessage(List<LlmMessage> messages, String systemPrompt) {
        return sendMessage(messages, systemPrompt, List.of());
    }

    public LlmResponse sendMessage(List<LlmMessage> messages, String systemPrompt, List<LlmTool> tools,
        Map<String, Object> metadata) {
        LlmRequest request = LlmRequest.builder()
            .model(model)
            .messages(messages == null ? List.of() : new ArrayList<>(messages))
            .systemPrompt(systemPrompt)
            .tools(tools == null ? List.of() : new ArrayList<>(tools))
            .maxTokens(defaultMaxTokens)
            .metadata(metadata == null ? Map.of() : metadata)
            .build();
        return adapter.complete(request);
    }
}
