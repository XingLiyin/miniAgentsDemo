package com.codex.miniagents.runtime.compaction;

import com.codex.miniagents.domain.memory.model.MemoryItem;

import java.util.ArrayList;
import java.util.List;

class TruncationStrategy {
    private final int keepLast;

    TruncationStrategy(int keepLast) {
        this.keepLast = Math.max(1, keepLast);
    }

    CompactionResult compact(List<MemoryItem> messages) {
        if (messages == null || messages.isEmpty()) {
            return new CompactionResult(List.of(), "");
        }
        if (messages.size() <= keepLast) {
            return new CompactionResult(new ArrayList<>(messages), "");
        }

        List<MemoryItem> kept = new ArrayList<>(messages.subList(messages.size() - keepLast, messages.size()));
        int dropped = messages.size() - kept.size();
        String summary = "[Truncated " + dropped + " older messages, kept " + kept.size() + "]";
        return new CompactionResult(kept, summary);
    }
}
