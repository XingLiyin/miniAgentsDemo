package com.codex.miniagents.runtime.compaction;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.memory.model.MemoryItem;
import com.codex.miniagents.llm.registry.LlmClientProvider;
import com.codex.miniagents.utils.TokenUtils;

import org.springframework.stereotype.Component;

import java.util.List;
import java.util.StringJoiner;

@Component
public class TokenBudgetComposedStrategy implements CompactionStrategy {
    private final SummarizationStrategy summarizationStrategy;
    private final TruncationStrategy truncationStrategy;
    private final int tokenBudget;

    public TokenBudgetComposedStrategy(LlmClientProvider llmClientProvider, MiniAgentsProperties properties) {
        this.tokenBudget = properties.getDefaultTokenBudget();
        this.truncationStrategy = new TruncationStrategy(properties.getDefaultShortWindowSize());
        this.summarizationStrategy = new SummarizationStrategy(
            llmClientProvider,
            properties.getAgentDefaultLlmName(),
            properties.getDefaultShortWindowSize()
        );
    }

    @Override
    public CompactionResult compact(List<MemoryItem> messages) {
        if (messages == null || messages.isEmpty()) {
            return new CompactionResult(List.of(), "");
        }

        String sample = buildSample(messages);
        if (TokenUtils.estimateTokens(sample) > tokenBudget) {
            return truncationStrategy.compact(messages);
        }

        CompactionResult summarized = summarizationStrategy.compact(messages);
        if (summarized.summaryText() == null || summarized.summaryText().isBlank()) {
            return truncationStrategy.compact(messages);
        }
        return summarized;
    }

    private String buildSample(List<MemoryItem> messages) {
        StringJoiner joiner = new StringJoiner(" ");
        for (MemoryItem item : messages) {
            if (item != null && item.getContent() != null) {
                joiner.add(item.getContent());
            }
        }
        return joiner.toString();
    }
}
