package com.codex.miniagents.llm.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.ArrayList;
import java.util.List;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class LlmMessage {
    private String role;

    private String content;

    private String toolCallId;

    @Builder.Default
    private List<ToolCallBlock> toolCalls = new ArrayList<>();
}
