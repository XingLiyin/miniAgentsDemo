package com.codex.miniagents.runtime.compaction;

import com.codex.miniagents.domain.memory.model.MemoryItem;
import com.codex.miniagents.llm.ChatClient;
import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.llm.registry.LlmClientProvider;

import java.util.ArrayList;
import java.util.List;
import java.util.StringJoiner;

class SummarizationStrategy {
    private static final String SYSTEM_PROMPT =
        "You summarize agent memory for future context. "
            + "Focus on completed work, decisions, open questions, and important facts. "
            + "Return only the summary text.";

    private final LlmClientProvider llmClientProvider;
    private final String llmName;
    private final TruncationStrategy fallback;

    SummarizationStrategy(LlmClientProvider llmClientProvider, String llmName, int keepLast) {
        this.llmClientProvider = llmClientProvider;
        this.llmName = llmName;
        this.fallback = new TruncationStrategy(keepLast);
    }

    CompactionResult compact(List<MemoryItem> messages) {
        if (messages == null || messages.isEmpty()) {
            return new CompactionResult(List.of(), "");
        }

        CompactionResult truncated = fallback.compact(messages);
        if (messages.size() <= truncated.messages().size()) {
            return truncated;
        }

        String summary = summarize(messages.subList(0, messages.size() - truncated.messages().size()));
        if (summary == null || summary.isBlank()) {
            return truncated;
        }
        return new CompactionResult(new ArrayList<>(truncated.messages()), summary.trim());
    }

    private String summarize(List<MemoryItem> droppedMessages) {
        try {
            ChatClient llmClient = llmClientProvider.get(llmName);
            List<LlmMessage> prompt = new ArrayList<>();
            prompt.add(LlmMessage.builder()
                .role("user")
                .content(renderDroppedMessages(droppedMessages))
                .build());
            return llmClient.sendMessage(prompt, SYSTEM_PROMPT).getText();
        } catch (Exception ignored) {
            return null;
        }
    }

    private String renderDroppedMessages(List<MemoryItem> droppedMessages) {
        if (droppedMessages == null || droppedMessages.isEmpty()) {
            return "No older messages to summarize.";
        }

        StringJoiner joiner = new StringJoiner("\n");
        joiner.add("Summarize the following older messages in 1-3 sentences:");
        for (MemoryItem item : droppedMessages) {
            joiner.add("- [" + safe(item.getRole()) + "] " + safe(item.getContent()));
        }
        return joiner.toString();
    }

    private String safe(String value) {
        return value == null ? "" : value;
    }
}
