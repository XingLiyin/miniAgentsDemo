package com.codex.miniagents.agenttemplate.definition;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ToolSpec {
    @Builder.Default
    private List<String> required = new ArrayList<>();

    @Builder.Default
    private List<String> forbidden = new ArrayList<>();

    public List<String> effective() {
        HashSet<String> excluded = new HashSet<>(forbidden == null ? List.of() : forbidden);
        List<String> result = new ArrayList<>();
        if (required == null) {
            return result;
        }
        for (String tool : required) {
            if (tool != null && !excluded.contains(tool)) {
                result.add(tool);
            }
        }
        return result;
    }
}
