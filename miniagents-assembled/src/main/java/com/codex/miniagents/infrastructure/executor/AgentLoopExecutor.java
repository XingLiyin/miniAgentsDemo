package com.codex.miniagents.infrastructure.executor;

import com.codex.miniagents.utils.AsyncUtils;

import org.springframework.stereotype.Component;

@Component
public class AgentLoopExecutor {

    public void submit(String sessionId, String agentId, Runnable runnable) {
        AsyncUtils.runAsync(runnable);
    }

    public void shutdown() {
        // AsyncUtils owns the shared executor lifecycle.
    }
}
