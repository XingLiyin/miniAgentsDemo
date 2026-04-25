package com.codex.miniagents.skills;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.skills.model.SkillDefinition;
import com.codex.miniagents.skills.model.SkillMetadata;
import com.codex.miniagents.skills.model.RemoteSkillSourceConfig;
import com.codex.miniagents.tools.mcp.AbstractMcpProvider;
import com.codex.miniagents.tools.mcp.McpStdioProvider;
import com.codex.miniagents.tools.mcp.McpStreamableHttpProvider;
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.model.ToolDefinition;

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
import java.util.Map;
import java.util.Set;
import java.util.concurrent.locks.ReentrantReadWriteLock;

@Component
@RequiredArgsConstructor
public class SkillRegistry {
    private static final Logger logger = LoggerFactory.getLogger(SkillRegistry.class);

    /**
     * 保持插入顺序，贴近 Python dict 的行为。
     */
    private final LinkedHashMap<String, SkillMetadata> localSkills = new LinkedHashMap<>();

    private final LinkedHashMap<String, SkillMcpConn> remoteConns = new LinkedHashMap<>();

    private final LinkedHashMap<String, String> remoteIndex = new LinkedHashMap<>();

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
            metadata.setSource("local");
            localSkills.put(metadata.getName(), metadata);
            logger.debug("SkillRegistry: registered local skill '{}'", metadata.getName());
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
            if (localSkills.containsKey(name)) {
                localSkills.remove(name);
                removed = true;
                logger.debug("SkillRegistry: unregistered local skill '{}'", name);
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
                metadata.setSource("local");
                localSkills.put(metadata.getName(), metadata);
                newlyRegistered.add(metadata);
                logger.debug("SkillRegistry: registered local skill '{}'", metadata.getName());
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
            return localSkills.get(name);
        } finally {
            lock.readLock().unlock();
        }
    }

    public List<SkillMetadata> listAll() {
        return listAll(null);
    }

    public List<SkillMetadata> listAll(CallContext ctx) {
        List<SkillMetadata> result = new ArrayList<>();
        lock.readLock().lock();
        try {
            result.addAll(localSkills.values());
        } finally {
            lock.readLock().unlock();
        }
        result.addAll(fetchRemoteLive(ctx));
        return result;
    }

    public void registerRemoteSource(RemoteSkillSourceConfig config) {
        if (config == null || config.getSourceName() == null || config.getSourceName().isBlank()) {
            throw new AppException(ErrorCode.INVALID_ARGUMENT, "Remote skill source name is required");
        }

        lock.readLock().lock();
        try {
            if (remoteConns.containsKey(config.getSourceName())) {
                throw new AppException(ErrorCode.MCP_ALREADY_EXISTS,
                    "Remote skill source '" + config.getSourceName() + "' already registered");
            }
        } finally {
            lock.readLock().unlock();
        }

        SkillMcpConn conn = buildConn(config);
        validateConnection(conn, config);

        lock.writeLock().lock();
        try {
            if (remoteConns.containsKey(config.getSourceName())) {
                conn.stop();
                throw new AppException(ErrorCode.MCP_ALREADY_EXISTS,
                    "Remote skill source '" + config.getSourceName() + "' already registered");
            }
            remoteConns.put(config.getSourceName(), conn);
            logger.info("SkillRegistry: registered remote source '{}'", config.getSourceName());
        } finally {
            lock.writeLock().unlock();
        }
    }

    public void unregisterRemoteSource(String sourceName) {
        SkillMcpConn conn;
        lock.writeLock().lock();
        try {
            remoteIndex.entrySet().removeIf(entry -> sourceName != null && sourceName.equals(entry.getValue()));
            conn = remoteConns.remove(sourceName);
        } finally {
            lock.writeLock().unlock();
        }
        if (conn != null) {
            conn.stop();
        }
        logger.info("SkillRegistry: unregistered remote source '{}'", sourceName);
    }

    @Nullable
    public SkillMcpConn getConn(String sourceName) {
        lock.readLock().lock();
        try {
            return remoteConns.get(sourceName);
        } finally {
            lock.readLock().unlock();
        }
    }

    @Nullable
    public SkillMcpConn getConnForSkill(String skillName) {
        lock.readLock().lock();
        try {
            String sourceName = remoteIndex.get(skillName);
            return sourceName == null ? null : remoteConns.get(sourceName);
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
        List<SkillMetadata> allSkills = listAll();
        if (allSkills.isEmpty()) {
            return "";
        }

        List<String> lines = new ArrayList<>();
        lines.add("## Available Skills (assign to tasks where appropriate)");
        for (SkillMetadata metadata : allSkills) {
            lines.add("- " + metadata.getName() + ": " + metadata.getDescription());
        }
        return String.join("\n", lines);
    }

    /**
     * 加载 Level 2 内容（SKILL.md 主体）。
     */
    @Nullable
    public SkillDefinition loadDefinition(String name) {
        return loadDefinition(name, null);
    }

    @Nullable
    public SkillDefinition loadDefinition(String name, CallContext ctx) {
        SkillMetadata meta;

        lock.readLock().lock();
        try {
            meta = localSkills.get(name);
        } finally {
            lock.readLock().unlock();
        }

        if (meta != null) {
            try {
                String instructions = skillLoader.loadInstructions(meta.getSkillDir());
                return SkillDefinition.builder().metadata(meta).instructions(instructions).build();
            } catch (Exception e) {
                logger.warn("SkillRegistry: failed to load local skill '{}': {}", name, e.getMessage());
                return null;
            }
        }

        SkillMcpConn conn = getConnForSkill(name);
        if (conn == null) {
            logger.warn("SkillRegistry: skill '{}' not found (call listAll() first to populate remote index)", name);
            return null;
        }
        String sourceName = remoteSourceNameFor(name);
        try {
            String instructions = conn.loadSkillMd(name, ctx);
            SkillMetadata remoteMeta = SkillMetadata.builder()
                .name(name)
                .description("")
                .triggers(List.of())
                .version("")
                .skillDir(Path.of(""))
                .source("remote")
                .remoteSourceName(sourceName)
                .build();
            return SkillDefinition.builder().metadata(remoteMeta).instructions(instructions).build();
        } catch (Exception e) {
            logger.warn("SkillRegistry: failed to load remote skill '{}': {}", name, e.getMessage());
            return null;
        }
    }

    private int size() {
        lock.readLock().lock();
        try {
            return localSkills.size();
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

    private List<SkillMetadata> fetchRemoteLive(CallContext ctx) {
        List<Map.Entry<String, SkillMcpConn>> conns;
        lock.readLock().lock();
        try {
            conns = new ArrayList<>(remoteConns.entrySet());
        } finally {
            lock.readLock().unlock();
        }

        List<SkillMetadata> result = new ArrayList<>();
        LinkedHashMap<String, String> nextIndex = new LinkedHashMap<>();
        for (Map.Entry<String, SkillMcpConn> entry : conns) {
            String sourceName = entry.getKey();
            SkillMcpConn conn = entry.getValue();
            try {
                List<Map<String, Object>> items = conn.listSkills(ctx);
                for (Map<String, Object> item : items) {
                    String name = stringValue(item.get("name"));
                    if (name.isBlank()) {
                        continue;
                    }
                    nextIndex.put(name, sourceName);
                    result.add(SkillMetadata.builder()
                        .name(name)
                        .description(stringValue(item.get("description")))
                        .triggers(List.of())
                        .version("")
                        .skillDir(Path.of(""))
                        .source("remote")
                        .remoteSourceName(sourceName)
                        .build());
                }
                logger.debug("SkillRegistry: live-fetched {} skill(s) from '{}'", result.size(), sourceName);
            } catch (Exception e) {
                logger.warn("SkillRegistry: live-fetch from '{}' failed: {}", sourceName, e.getMessage());
            }
        }

        lock.writeLock().lock();
        try {
            remoteIndex.clear();
            remoteIndex.putAll(nextIndex);
        } finally {
            lock.writeLock().unlock();
        }
        return result;
    }

    private void validateConnection(SkillMcpConn conn, RemoteSkillSourceConfig config) {
        List<ToolDefinition> toolDefinitions;
        try {
            toolDefinitions = conn.getProvider().listDefinitions();
        } catch (Exception e) {
            conn.stop();
            throw new AppException(ErrorCode.MCP_INVALID_CONFIG, "Failed to list MCP tools: " + e.getMessage());
        }

        Map<String, ToolDefinition> toolMap = new LinkedHashMap<>();
        for (ToolDefinition definition : toolDefinitions) {
            toolMap.put(definition.getName(), definition);
        }
        List<String> requiredTools = List.of(
            toolName(config.getMcpToolListSkills(), "listSkills"),
            toolName(config.getMcpToolLoadSkillMd(), "loadSkillMd"),
            toolName(config.getMcpToolGetSkillFiles(), "getSkillFiles"),
            toolName(config.getMcpToolLoadSkillReference(), "loadSkillReference"),
            toolName(config.getMcpToolExecSkillScript(), "execSkillScript")
        );
        List<String> missingTools = requiredTools.stream()
            .filter(name -> !toolMap.containsKey(name))
            .toList();
        if (!missingTools.isEmpty()) {
            conn.stop();
            throw new AppException(ErrorCode.MCP_INVALID_CONFIG,
                "MCP server missing required tools: " + missingTools);
        }

        validateToolSchema(conn, toolMap, toolName(config.getMcpToolLoadSkillMd(), "loadSkillMd"), Set.of("skillName"));
        validateToolSchema(conn, toolMap, toolName(config.getMcpToolGetSkillFiles(), "getSkillFiles"), Set.of("skillName"));
        validateToolSchema(conn, toolMap, toolName(config.getMcpToolLoadSkillReference(), "loadSkillReference"),
            Set.of("skillName", "referencePath"));
        validateToolSchema(conn, toolMap, toolName(config.getMcpToolExecSkillScript(), "execSkillScript"),
            Set.of("skillName", "scriptPath"));
    }

    private void validateToolSchema(SkillMcpConn conn, Map<String, ToolDefinition> toolMap, String toolName,
        Set<String> requiredParams) {
        ToolDefinition definition = toolMap.get(toolName);
        if (definition == null || definition.getInputSchema() == null) {
            return;
        }
        Map<String, Object> properties = definition.getInputSchema().getProperties();
        List<String> missingParams = requiredParams.stream()
            .filter(param -> properties == null || !properties.containsKey(param))
            .toList();
        if (!missingParams.isEmpty()) {
            conn.stop();
            throw new AppException(ErrorCode.MCP_INVALID_CONFIG,
                "Tool '" + toolName + "' missing params: " + missingParams);
        }
    }

    private SkillMcpConn buildConn(RemoteSkillSourceConfig config) {
        AbstractMcpProvider provider;
        if ("http".equals(config.getMcpType())) {
            provider = new McpStreamableHttpProvider(config.getSourceName(), config.getMcpUrl(), config.getMcpTimeout());
        } else if ("stdio".equals(config.getMcpType())) {
            provider = new McpStdioProvider(config.getSourceName(), config.getMcpCommand(), config.getMcpArgs(),
                config.getMcpEnv());
        } else {
            throw new AppException(ErrorCode.MCP_INVALID_CONFIG,
                "Unsupported mcp_type: '" + config.getMcpType() + "'");
        }
        provider.start();
        return new SkillMcpConn(
            provider,
            config.getMcpToolListSkills(),
            config.getMcpToolLoadSkillMd(),
            config.getMcpToolGetSkillFiles(),
            config.getMcpToolLoadSkillReference(),
            config.getMcpToolExecSkillScript()
        );
    }

    private String remoteSourceNameFor(String skillName) {
        lock.readLock().lock();
        try {
            return remoteIndex.get(skillName);
        } finally {
            lock.readLock().unlock();
        }
    }

    private String stringValue(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private String toolName(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }
}
