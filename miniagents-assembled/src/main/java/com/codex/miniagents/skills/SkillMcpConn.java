package com.codex.miniagents.skills;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.tools.mcp.AbstractMcpProvider;
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.model.ToolResult;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import lombok.extern.slf4j.Slf4j;

import java.util.List;
import java.util.Map;

@Slf4j
public class SkillMcpConn {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private final AbstractMcpProvider provider;

    private final String mcpToolListSkills;

    private final String mcpToolLoadSkillMd;

    private final String mcpToolGetSkillFiles;

    private final String mcpToolLoadSkillReference;

    private final String mcpToolExecSkillScript;

    public SkillMcpConn(AbstractMcpProvider provider, String mcpToolListSkills, String mcpToolLoadSkillMd,
        String mcpToolGetSkillFiles, String mcpToolLoadSkillReference, String mcpToolExecSkillScript) {
        this.provider = provider;
        this.mcpToolListSkills = defaultString(mcpToolListSkills, "listSkills");
        this.mcpToolLoadSkillMd = defaultString(mcpToolLoadSkillMd, "loadSkillMd");
        this.mcpToolGetSkillFiles = defaultString(mcpToolGetSkillFiles, "getSkillFiles");
        this.mcpToolLoadSkillReference = defaultString(mcpToolLoadSkillReference, "loadSkillReference");
        this.mcpToolExecSkillScript = defaultString(mcpToolExecSkillScript, "execSkillScript");
    }

    public AbstractMcpProvider getProvider() {
        return provider;
    }

    public List<Map<String, Object>> listSkills(CallContext ctx) {
        ToolResult result = provider.call(mcpToolListSkills, Map.of(), ctx);
        try {
            return OBJECT_MAPPER.readValue(result.getContent(), new TypeReference<>() {});
        } catch (Exception e) {
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR,
                "Remote skill list response is not valid JSON: " + e.getMessage());
        }
    }

    public String loadSkillMd(String skillName, CallContext ctx) {
        return provider.call(mcpToolLoadSkillMd, Map.of("skillName", defaultString(skillName, "")), ctx).getContent();
    }

    public String getSkillFiles(String skillName, String pattern, int limit, CallContext ctx) {
        return provider.call(mcpToolGetSkillFiles, Map.of(
            "skillName", defaultString(skillName, ""),
            "pattern", defaultString(pattern, "**/*"),
            "limit", limit
        ), ctx).getContent();
    }

    public String loadSkillReference(String skillName, String referencePath, CallContext ctx) {
        return provider.call(mcpToolLoadSkillReference, Map.of(
            "skillName", defaultString(skillName, ""),
            "referencePath", defaultString(referencePath, "")
        ), ctx).getContent();
    }

    public ToolResult execSkillScript(String skillName, String scriptPath, String args, CallContext ctx) {
        return provider.call(mcpToolExecSkillScript, Map.of(
            "skillName", defaultString(skillName, ""),
            "scriptPath", defaultString(scriptPath, ""),
            "args", defaultString(args, "")
        ), ctx);
    }

    public void stop() {
        try {
            provider.stop();
        } catch (Exception e) {
            log.warn("SkillMcpConn.stop: error while stopping provider", e);
        }
    }

    private String defaultString(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }
}
