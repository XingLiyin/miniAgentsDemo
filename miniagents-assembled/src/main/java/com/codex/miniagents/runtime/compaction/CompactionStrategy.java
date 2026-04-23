package com.codex.miniagents.runtime.compaction;

import com.codex.miniagents.domain.memory.model.MemoryItem;

import java.util.List;

public interface CompactionStrategy {
    CompactionResult compact(List<MemoryItem> messages);
}
