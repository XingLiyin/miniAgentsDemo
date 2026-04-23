package com.codex.miniagents.tools.mcp;

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
public class McpFunctionDescriptor {
    private String name;

    @Builder.Default
    private String description = "";

    @Builder.Default
    private Map<String, Object> inputSchema = new HashMap<>();

    @Builder.Default
    private Map<String, Object> metadata = new HashMap<>();
}
