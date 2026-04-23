package com.codex.miniagents.runtime.prompt;

public final class PromptBuilderFactory {
    private PromptBuilderFactory() {
    }

    public static ActorPromptBuilder forActor() {
        return new ActorPromptBuilder();
    }

    public static ObserverPromptBuilder forObserver() {
        return new ObserverPromptBuilder();
    }
}
