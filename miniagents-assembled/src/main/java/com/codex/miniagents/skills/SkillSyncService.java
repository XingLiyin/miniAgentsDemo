package com.codex.miniagents.skills;

import com.codex.miniagents.skills.model.SkillMetadata;

import lombok.RequiredArgsConstructor;

import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class SkillSyncService {
    private final SkillStoreClient skillStoreClient;

    /**
     * Skill 注册后调用；推送到外部 DB。
     */
    public void onSkillsAdded(List<SkillMetadata> metadataList) {
        if (metadataList == null || metadataList.isEmpty()) {
            return;
        }

        List<Map<String, Object>> records = metadataList.stream().map(this::toRecord).toList();

        skillStoreClient.upsert(records);
    }

    /**
     * Skill 注销后调用；从外部 DB 删除。
     */
    public void onSkillsRemoved(List<String> names) {
        if (names == null || names.isEmpty()) {
            return;
        }

        skillStoreClient.delete(names);
    }

    private Map<String, Object> toRecord(SkillMetadata metadata) {
        Map<String, Object> record = new HashMap<>();
        record.put("name", metadata.getName());
        record.put("description", metadata.getDescription());
        record.put("triggers", metadata.getTriggers());
        record.put("version", metadata.getVersion());
        return record;
    }
}
