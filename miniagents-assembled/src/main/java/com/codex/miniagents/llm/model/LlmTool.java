package com.codex.miniagents.llm.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class LlmTool {
    @Builder.Default
    private String type = "function";

    private String name;

    private String description;

    @Builder.Default
    private InputSchema inputSchema = new InputSchema();

    private Object outputSchema;

    public String toPromptText() {
        String toolName = name == null ? "" : name;
        List<String> params = new ArrayList<>();
        List<String> required = inputSchema == null || inputSchema.getRequired() == null ? List.of()
            : inputSchema.getRequired();
        Map<String, Object> properties = inputSchema == null || inputSchema.getProperties() == null ? Map.of()
            : inputSchema.getProperties();

        for (Map.Entry<String, Object> entry : properties.entrySet()) {
            String pname = entry.getKey();
            Map<String, Object> pinfo = entry.getValue() instanceof Map<?, ?> map
                ? castMap(map)
                : Map.of();
            String ptype = pinfo.getOrDefault("type", "any").toString();
            Object defaultValue = pinfo.get("default");

            if (required.contains(pname)) {
                params.add(pname + ": " + ptype);
            } else if (defaultValue != null) {
                params.add(pname + ": " + ptype + " = " + String.valueOf(defaultValue));
            } else {
                params.add(pname + "?: " + ptype);
            }
        }

        String signature = toolName + "(" + String.join(", ", params) + ")";
        return (description == null || description.isBlank()) ? signature : signature + " — " + description;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> castMap(Map<?, ?> map) {
        return (Map<String, Object>) map;
    }
}
