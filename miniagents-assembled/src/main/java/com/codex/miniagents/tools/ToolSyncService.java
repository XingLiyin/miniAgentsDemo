package com.codex.miniagents.tools;

import com.codex.miniagents.dto.request.ToolStoreUpsertRequest;
import com.codex.miniagents.tools.model.ToolDefinition;

import lombok.RequiredArgsConstructor;

import org.springframework.stereotype.Service;

import java.util.List;

@Service
@RequiredArgsConstructor
public class ToolSyncService {
    private final ToolStoreClient toolStoreClient;

    public void onToolsAdded(List<ToolDefinition> toolDefinitions, String provider) {
        if (toolDefinitions == null || toolDefinitions.isEmpty()) {
            return;
        }
        toolStoreClient.upsert(new ToolStoreUpsertRequest(toolDefinitions, provider));
    }

    public void onToolsRemoved(List<String> names) {
        if (names == null || names.isEmpty()) {
            return;
        }
        toolStoreClient.delete(names);
    }
}
