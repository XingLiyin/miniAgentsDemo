package com.codex.miniagents.tools.model;

import com.codex.miniagents.llm.model.InputSchema;
import com.codex.miniagents.llm.model.LlmTool;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.HashMap;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ToolDefinition {
    private String name;

    private String description;

    @Builder.Default
    private InputSchema inputSchema = new InputSchema();

    @Builder.Default
    private Map<String, Object> metadata = new HashMap<>();

    private ToolHandler handler;

    public LlmTool toLlmTool() {
        return LlmTool.builder().type("function").name(name).description(description).inputSchema(inputSchema).build();
    }
}
