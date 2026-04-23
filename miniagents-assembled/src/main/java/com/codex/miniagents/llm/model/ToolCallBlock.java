package com.codex.miniagents.llm.model;

import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.LinkedHashMap;
import java.util.Map;

@Getter
@Setter
@EqualsAndHashCode(callSuper = true)
@NoArgsConstructor
public class ToolCallBlock extends LlmContentBlock {
    private String id;

    private String name;

    private Map<String, Object> input = new LinkedHashMap<>();

    private String toolType = "function";

    private Map<String, Object> raw = new LinkedHashMap<>();

    public ToolCallBlock(String type, String id, String name, Map<String, Object> input, String toolType,
        Map<String, Object> raw) {
        super(type);
        this.id = id;
        this.name = name;
        if (input != null) {
            this.input = input;
        }
        this.toolType = toolType;
        if (raw != null) {
            this.raw = raw;
        }
    }
}
