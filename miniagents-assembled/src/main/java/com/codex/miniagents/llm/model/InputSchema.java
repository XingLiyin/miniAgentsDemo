package com.codex.miniagents.llm.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class InputSchema {
    @Builder.Default
    private String type = "object";

    @Builder.Default
    private Map<String, Object> properties = new LinkedHashMap<>();

    @Builder.Default
    private List<String> required = List.of();

    public Map<String, Object> toDict() {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("type", type);
        map.put("properties", properties == null ? Map.of() : properties);
        map.put("required", required == null ? List.of() : required);
        return map;
    }
}
