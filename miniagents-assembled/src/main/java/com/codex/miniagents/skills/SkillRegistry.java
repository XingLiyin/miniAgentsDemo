package com.codex.miniagents.skills;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.skills.model.SkillDefinition;
import com.codex.miniagents.skills.model.SkillMetadata;

import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.lang.Nullable;
import org.springframework.stereotype.Component;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.concurrent.locks.ReentrantReadWriteLock;

@Component
@RequiredArgsConstructor
public class SkillRegistry {
    private static final Logger logger = LoggerFactory.getLogger(SkillRegistry.class);

    /**
     * 保持插入顺序，贴近 Python dict 的行为。
     */
    private final LinkedHashMap<String, SkillMetadata> skills = new LinkedHashMap<>();

    /**
     * 基本线程安全即可，不追求强一致性。
     */
    private final ReentrantReadWriteLock lock = new ReentrantReadWriteLock();

    private final SkillLoader skillLoader;

    private final SkillSyncService skillSyncService;

    private final MiniAgentsProperties properties;

    /**
     * Spring 启动时初始化 skill。
     */
    @PostConstruct
    public void init() {
        loadFromDir(properties.getSkillsDir());
    }

    /**
     * 注册单个 SkillMetadata（重复注册会覆盖）。
     */
    public void register(SkillMetadata metadata) {
        if (metadata == null) {
            return;
        }

        lock.writeLock().lock();
        try {
            skills.put(metadata.getName(), metadata);
            logger.debug("SkillRegistry: registered skill '{}'", metadata.getName());
        } finally {
            lock.writeLock().unlock();
        }

        syncAdded(List.of(metadata));
    }

    /**
     * 注销指定 Skill 并同步到外部 DB。
     */
    public void unregister(String name) {
        boolean removed = false;

        lock.writeLock().lock();
        try {
            if (skills.containsKey(name)) {
                skills.remove(name);
                removed = true;
                logger.debug("SkillRegistry: unregistered skill '{}'", name);
            }
        } finally {
            lock.writeLock().unlock();
        }

        if (removed) {
            syncRemoved(List.of(name));
        }
    }

    /**
     * 扫描目录，批量注册所有发现的 Skill（一次性批量同步到外部 DB）。
     */
    public void loadFromDir(Path skillsDir) {
        List<SkillMetadata> scanned = skillLoader.scan(skillsDir);
        if (scanned.isEmpty()) {
            logger.info("SkillRegistry: loaded {} skill(s) from '{}'", size(), skillsDir);
            return;
        }

        List<SkillMetadata> newlyRegistered = new ArrayList<>();

        lock.writeLock().lock();
        try {
            for (SkillMetadata metadata : scanned) {
                skills.put(metadata.getName(), metadata);
                newlyRegistered.add(metadata);
                logger.debug("SkillRegistry: registered skill '{}'", metadata.getName());
            }
        } finally {
            lock.writeLock().unlock();
        }

        syncAdded(newlyRegistered);
        logger.info("SkillRegistry: loaded {} skill(s) from '{}'", size(), skillsDir);
    }

    @Nullable
    public SkillMetadata getMetadata(String name) {
        lock.readLock().lock();
        try {
            return skills.get(name);
        } finally {
            lock.readLock().unlock();
        }
    }

    public List<SkillMetadata> listAll() {
        lock.readLock().lock();
        try {
            return new ArrayList<>(skills.values());
        } finally {
            lock.readLock().unlock();
        }
    }

    /**
     * 生成 Available Skills 文本块（Level 1 内容）。
     * <p>
     * 格式：
     * ## Available Skills (assign to tasks where appropriate)
     * - code-review: 审查代码质量...
     */
    public String getMetadataBlock() {
        lock.readLock().lock();
        try {
            if (skills.isEmpty()) {
                return "";
            }

            List<String> lines = new ArrayList<>();
            lines.add("## Available Skills (assign to tasks where appropriate)");
            for (SkillMetadata metadata : skills.values()) {
                lines.add("- " + metadata.getName() + ": " + metadata.getDescription());
            }
            return String.join("\n", lines);
        } finally {
            lock.readLock().unlock();
        }
    }

    /**
     * 加载 Level 2 内容（SKILL.md 主体）。
     */
    @Nullable
    public SkillDefinition loadDefinition(String name) {
        SkillMetadata meta;

        lock.readLock().lock();
        try {
            meta = skills.get(name);
        } finally {
            lock.readLock().unlock();
        }

        if (meta == null) {
            logger.warn("SkillRegistry: skill '{}' not found", name);
            return null;
        }

        try {
            String instructions = skillLoader.loadInstructions(meta.getSkillDir());
            return SkillDefinition.builder().metadata(meta).instructions(instructions).build();
        } catch (Exception e) {
            logger.warn("SkillRegistry: failed to load instructions for '{}': {}", name, e.getMessage());
            return null;
        }
    }

    private int size() {
        lock.readLock().lock();
        try {
            return skills.size();
        } finally {
            lock.readLock().unlock();
        }
    }

    private void syncAdded(List<SkillMetadata> metadatas) {
        try {
            skillSyncService.onSkillsAdded(metadatas);
        } catch (Exception e) {
            logger.warn("SkillRegistry: sync-added failed", e);
        }
    }

    private void syncRemoved(List<String> names) {
        try {
            skillSyncService.onSkillsRemoved(names);
        } catch (Exception e) {
            logger.warn("SkillRegistry: sync-removed failed for {}", names, e);
        }
    }
}
