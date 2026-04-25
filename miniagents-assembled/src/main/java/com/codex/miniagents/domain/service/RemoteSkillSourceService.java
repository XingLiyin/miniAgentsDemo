package com.codex.miniagents.domain.service;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.infrastructure.storage.file.RemoteSkillSourceStore;
import com.codex.miniagents.skills.SkillRegistry;
import com.codex.miniagents.skills.model.RemoteSkillSourceConfig;

import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

import org.springframework.stereotype.Service;

import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
public class RemoteSkillSourceService {
    private final SkillRegistry skillRegistry;

    private final RemoteSkillSourceStore store;

    @PostConstruct
    public void init() {
        try {
            restoreAll();
        } catch (Exception e) {
            log.warn("RemoteSkillSourceService: failed to restore remote skill sources on startup", e);
        }
    }

    public RemoteSkillSourceConfig register(RemoteSkillSourceConfig config) {
        String sourceName = config == null ? "" : config.getSourceName();
        if (sourceName == null || sourceName.isBlank()) {
            throw new AppException(ErrorCode.INVALID_ARGUMENT, "Remote skill source name is required");
        }
        if (store.get(sourceName).isPresent()) {
            throw new AppException(ErrorCode.SKILL_SOURCE_ALREADY_EXISTS,
                "Remote skill source '" + sourceName + "' already registered");
        }

        skillRegistry.registerRemoteSource(config);
        store.save(config);
        log.info("RemoteSkillSourceService: registered '{}'", sourceName);
        return config;
    }

    public void delete(String sourceName) {
        sourceName = requireSourceName(sourceName);
        if (store.get(sourceName).isEmpty()) {
            throw new AppException(ErrorCode.SKILL_SOURCE_NOT_FOUND,
                "Remote skill source '" + sourceName + "' not found");
        }

        skillRegistry.unregisterRemoteSource(sourceName);
        store.delete(sourceName);
    }

    public RemoteSkillSourceConfig get(String sourceName) {
        String resolvedSourceName = requireSourceName(sourceName);
        return store.get(resolvedSourceName)
            .orElseThrow(() -> new AppException(ErrorCode.SKILL_SOURCE_NOT_FOUND,
                "Remote skill source '" + resolvedSourceName + "' not found"));
    }

    public List<RemoteSkillSourceConfig> listAll() {
        return store.listAll();
    }

    public void restoreAll() {
        List<RemoteSkillSourceConfig> configs = store.listAll();
        if (configs.isEmpty()) {
            return;
        }

        log.info("RemoteSkillSourceService.restoreAll: restoring {} source(s)", configs.size());
        for (RemoteSkillSourceConfig config : configs) {
            String sourceName = config.getSourceName() == null ? "<unknown>" : config.getSourceName();
            try {
                skillRegistry.registerRemoteSource(config);
                log.info("RemoteSkillSourceService.restoreAll: restored '{}'", sourceName);
            } catch (Exception e) {
                log.warn("RemoteSkillSourceService.restoreAll: failed to restore '{}', skipping", sourceName, e);
            }
        }
    }

    private String requireSourceName(String sourceName) {
        if (sourceName == null || sourceName.isBlank()) {
            throw new AppException(ErrorCode.INVALID_ARGUMENT, "Remote skill source name is required");
        }
        return sourceName;
    }
}
