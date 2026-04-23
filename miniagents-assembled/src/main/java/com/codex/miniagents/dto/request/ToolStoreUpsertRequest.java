package com.codex.miniagents.dto.request;

import com.codex.miniagents.tools.model.ToolDefinition;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.List;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ToolStoreUpsertRequest {
    private List<ToolStoreUpsertRecord> tools;

    public ToolStoreUpsertRequest(List<ToolDefinition> toolDefinitions, String provider) {
        this.tools = toolDefinitions.stream()
            .map(td -> ToolStoreUpsertRecord.builder()
                .name(td.getName())
                .description(td.getDescription())
                .inputSchema(td.getInputSchema() == null ? Map.of() : td.getInputSchema().toDict())
                .provider(provider)
                .build())
            .toList();
    }

    @Getter
    @Setter
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class ToolStoreUpsertRecord {
        private String name;

        private String description;

        @JsonIgnoreProperties("input_schema")
        private Map<String, Object> inputSchema;

        private String provider;
    }
}
