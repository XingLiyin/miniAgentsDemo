package com.codex.miniagents.domain.model.agent;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.Getter;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Getter
@JsonIgnoreProperties(ignoreUnknown = true)
public class Agent {
    private String id;

    private String sessionId;

    private String templateId;

    private String name;

    private AgentStatus status;

    private String soulMd = "";

    private String roleMd = "";

    private List<String> actToolList = new ArrayList<>();

    private List<String> observeToolList = new ArrayList<>();

    private List<String> mcpActServers = new ArrayList<>();

    private List<String> mcpObserveServers = new ArrayList<>();

    private List<String> skillList = new ArrayList<>();

    private String soulPath;

    private Map<String, Object> settings = new LinkedHashMap<>();

    private LoopGuard loopGuard = new LoopGuard();

    private boolean inheritMemory = true;

    private String llmName = "";

    private String llmModel = "";

    private boolean hasSpawnPermission = false;

    private int spawnDepth = 0;

    private String parentTaskId;

    private Instant createdAt;

    private Instant updatedAt;

    public Agent() {
    }

    public Agent(String id, String sessionId, String templateId, String name, AgentStatus status,
        List<String> actToolList, List<String> observeToolList, List<String> skillList, String soulPath,
        LoopGuard loopGuard, String llmName, String llmModel, Instant createdAt, Instant updatedAt) {
        this.id = id;
        this.sessionId = sessionId;
        this.templateId = templateId;
        this.name = name;
        this.status = status;
        this.actToolList = actToolList == null ? new ArrayList<>() : new ArrayList<>(actToolList);
        this.observeToolList = observeToolList == null ? new ArrayList<>() : new ArrayList<>(observeToolList);
        this.skillList = skillList == null ? new ArrayList<>() : new ArrayList<>(skillList);
        this.soulPath = soulPath;
        this.settings = new LinkedHashMap<>();
        this.loopGuard = loopGuard == null ? new LoopGuard() : loopGuard;
        this.inheritMemory = true;
        this.llmName = llmName == null ? "" : llmName;
        this.llmModel = llmModel == null ? "" : llmModel;
        this.hasSpawnPermission = false;
        this.spawnDepth = 0;
        this.parentTaskId = null;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    public void markRunning() {
        this.status = AgentStatus.RUNNING;
    }

    public void markFinished() {
        this.status = AgentStatus.FINISHED;
    }

    public void markWaiting() {
        this.status = AgentStatus.WAITING;
    }

    public void markFailed() {
        this.status = AgentStatus.FAILED;
    }

    public void increaseTurnsUsed() {
        if (this.loopGuard != null) {
            this.loopGuard.incrementTurnsUsed();
        }
    }

    public void setId(String id) {
        this.id = id;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public void setTemplateId(String templateId) {
        this.templateId = templateId;
    }

    public void setName(String name) {
        this.name = name;
    }

    public void setStatus(AgentStatus status) {
        this.status = status;
    }

    public void setSoulMd(String soulMd) {
        this.soulMd = soulMd == null ? "" : soulMd;
    }

    public void setRoleMd(String roleMd) {
        this.roleMd = roleMd == null ? "" : roleMd;
    }

    public void setActToolList(List<String> actToolList) {
        this.actToolList = actToolList == null ? new ArrayList<>() : new ArrayList<>(actToolList);
    }

    public void setObserveToolList(List<String> observeToolList) {
        this.observeToolList = observeToolList == null ? new ArrayList<>() : new ArrayList<>(observeToolList);
    }

    public void setMcpActServers(List<String> mcpActServers) {
        this.mcpActServers = mcpActServers == null ? new ArrayList<>() : new ArrayList<>(mcpActServers);
    }

    public void setMcpObserveServers(List<String> mcpObserveServers) {
        this.mcpObserveServers = mcpObserveServers == null ? new ArrayList<>() : new ArrayList<>(mcpObserveServers);
    }

    public void setSkillList(List<String> skillList) {
        this.skillList = skillList == null ? new ArrayList<>() : new ArrayList<>(skillList);
    }

    public void setSoulPath(String soulPath) {
        this.soulPath = soulPath;
    }

    public void setSettings(Map<String, Object> settings) {
        this.settings = settings == null ? new LinkedHashMap<>() : new LinkedHashMap<>(settings);
    }

    public void setLoopGuard(LoopGuard loopGuard) {
        this.loopGuard = loopGuard;
    }

    public void setInheritMemory(boolean inheritMemory) {
        this.inheritMemory = inheritMemory;
    }

    public void setLlmName(String llmName) {
        this.llmName = llmName;
    }

    public void setLlmModel(String llmModel) {
        this.llmModel = llmModel == null ? "" : llmModel;
    }

    public void setHasSpawnPermission(boolean hasSpawnPermission) {
        this.hasSpawnPermission = hasSpawnPermission;
    }

    public void setSpawnDepth(int spawnDepth) {
        this.spawnDepth = spawnDepth;
    }

    public void setParentTaskId(String parentTaskId) {
        this.parentTaskId = parentTaskId;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }

    public void setUpdatedAt(Instant updatedAt) {
        this.updatedAt = updatedAt;
    }
}
