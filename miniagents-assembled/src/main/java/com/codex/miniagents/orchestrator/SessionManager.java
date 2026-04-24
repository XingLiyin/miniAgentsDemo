package com.codex.miniagents.orchestrator;

import com.codex.miniagents.agenttemplate.AgentTemplateRegistry;
import com.codex.miniagents.agenttemplate.definition.AgentDefContent;
import com.codex.miniagents.common.SseBus;
import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.dto.request.InitialTaskConfig;
import com.codex.miniagents.domain.model.AgentTemplate;
import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.agent.AgentStatus;
import com.codex.miniagents.domain.model.agent.LoopGuard;
import com.codex.miniagents.domain.model.session.Session;
import com.codex.miniagents.domain.model.session.SessionStatus;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.service.AgentTemplateService;
import com.codex.miniagents.domain.memory.service.MemoryService;
import com.codex.miniagents.domain.service.BlackboardService;
import com.codex.miniagents.domain.service.SessionService;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.infrastructure.storage.repository.AgentRepository;
import com.codex.miniagents.infrastructure.storage.repository.ToolCallRepository;
import com.codex.miniagents.infrastructure.storage.file.EventStore;
import com.codex.miniagents.runtime.HitlStore;
import com.codex.miniagents.utils.IdUtils;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Slf4j
@Component
public class SessionManager {
    private final SessionService sessionService;

    private final AgentTemplateService templateService;

    private final AgentRepository agentRepository;

    private final TaskService taskService;

    private final MemoryService memoryService;

    private final BlackboardService blackboardService;

    private final ToolCallRepository toolCallRepository;

    private final LifecycleManager lifecycleManager;
    private final AgentTemplateRegistry templateRegistry;

    private final String defaultLlmName;

    private final int defaultTokenBudget;

    private final int defaultRootMaxTurns;
    private final String defaultTemplateName;

    public SessionManager(SessionService sessionService, AgentTemplateService templateService,
        AgentRepository agentRepository, TaskService taskService, MemoryService memoryService,
        BlackboardService blackboardService, ToolCallRepository toolCallRepository, LifecycleManager lifecycleManager,
        AgentTemplateRegistry templateRegistry,
        MiniAgentsProperties properties) {
        this.sessionService = sessionService;
        this.templateService = templateService;
        this.agentRepository = agentRepository;
        this.taskService = taskService;
        this.memoryService = memoryService;
        this.blackboardService = blackboardService;
        this.toolCallRepository = toolCallRepository;
        this.lifecycleManager = lifecycleManager;
        this.templateRegistry = templateRegistry;
        this.defaultLlmName = properties.getAgentDefaultLlmName();
        this.defaultTokenBudget = properties.getDefaultTokenBudget();
        this.defaultRootMaxTurns = properties.getDefaultRootMaxTurns();
        this.defaultTemplateName = properties.getDefaultAgentTemplateName();
    }

    public CreateSessionResult createSession(String userPrompt, String templateId, Integer tokenBudget,
        Integer rootMaxTurns, String llmName, String llmModel, InitialTaskConfig initialTask) {
        Session session = sessionService.create(userPrompt, templateId,
            tokenBudget == null ? defaultTokenBudget : tokenBudget,
            rootMaxTurns == null ? defaultRootMaxTurns : rootMaxTurns);

        AgentTemplate template = null;
        if (templateId != null && !templateId.isBlank()) {
            try {
                template = templateService.get(templateId);
            } catch (AppException ignored) {
                log.warn("Template {} not found, using defaults", templateId);
            }
        }
        if (template == null) {
            template = templateService.getByName(defaultTemplateName);
            if (template == null) {
                log.warn("Default template '{}' not found, using settings fallback", defaultTemplateName);
            }
        }

        Agent agent = buildRootAgent(session, template, llmName, llmModel);
        agentRepository.save(agent);
        sessionService.setRootAgent(session.getId(), agent.getId());
        try {
            SseBus.getInstance().push(session.getId(), java.util.Map.of(
                "type", "message",
                "role", "user",
                "content", userPrompt,
                "created_at", java.time.Instant.now().toString()
            ));
        } catch (Exception ignored) {
        }
        lifecycleManager.initSession(session.getId());
        createInitialTask(session.getId(), agent.getId(), userPrompt, initialTask);
        return new CreateSessionResult(session, agent.getId());
    }

    public void scheduleLoop(String sessionId, String agentId) {
        List<Task> pending = taskService.listPending(sessionId);
        if (pending.isEmpty()) {
            log.warn("No pending task found for session {}", sessionId);
            return;
        }
        lifecycleManager.scheduleInitialTask(sessionId, agentId, pending.get(0).getId());
    }

    public Session continueSession(String sessionId, String userMessage) {
        return continueSession(sessionId, userMessage, null);
    }

    public Session continueSession(String sessionId, String userMessage, InitialTaskConfig initialTask) {
        Session session = sessionService.get(sessionId);
        session.setUserPrompt(userMessage);
        sessionService.save(session);
        if (session.getStatus() == SessionStatus.CANCELED) {
            throw new AppException(ErrorCode.SESSION_CANCELED,
                "Session " + sessionId + " is canceled and cannot be continued");
        }

        try {
            SseBus.getInstance().push(sessionId, java.util.Map.of(
                "type", "message",
                "role", "user",
                "content", userMessage,
                "created_at", java.time.Instant.now().toString()
            ));
        } catch (Exception ignored) {
        }

        if (session.getStatus() == SessionStatus.QUEUED || session.getStatus() == SessionStatus.RUNNING) {
            return session;
        }

        if (session.getRootAgentId() == null || session.getRootAgentId().isBlank()) {
            throw new AppException(ErrorCode.AGENT_NOT_FOUND, "Session " + sessionId + " has no root agent");
        }
        Agent agent = agentRepository.findById(session.getRootAgentId()).orElse(null);
        if (agent == null) {
            throw new AppException(ErrorCode.AGENT_NOT_FOUND, "Root agent " + session.getRootAgentId() + " not found");
        }
        agent.setStatus(AgentStatus.IDLE);
        if (agent.getLoopGuard() != null) {
            agent.getLoopGuard().setTurnsUsed(0);
            agent.getLoopGuard().setMaxTurns(session.getRootMaxTurns());
        }
        agent.setUpdatedAt(Instant.now());
        agentRepository.save(agent);
        sessionService.transition(sessionId, SessionStatus.QUEUED);
        lifecycleManager.initSession(sessionId);
        if (initialTask == null) {
            createInitialTask(sessionId, agent.getId(), userMessage, null);
        } else {
            createInitialTask(sessionId, agent.getId(), userMessage, initialTask);
        }
        scheduleLoop(sessionId, agent.getId());
        return sessionService.get(sessionId);
    }

    public Session answerInput(String sessionId, String content) {
        Session session = sessionService.get(sessionId);
        if (session.getStatus() != SessionStatus.WAITING_INPUT) {
            throw new AppException(ErrorCode.INVALID_STATE, "Session " + sessionId + " is not waiting for input");
        }

        HitlStore.getInstance().submit(sessionId, content);

        try {
            SseBus.getInstance().push(sessionId, java.util.Map.of(
                "type", "message",
                "role", "user",
                "content", content,
                "created_at", java.time.Instant.now().toString()
            ));
        } catch (Exception ignored) {
        }

        // Working thread resumes on its own and transitions back to RUNNING.
        return sessionService.get(sessionId);
    }

    public Session cancelSession(String sessionId) {
        return sessionService.transition(sessionId, SessionStatus.CANCELED);
    }

    public void deleteSession(String sessionId) {
        Session session = sessionService.get(sessionId);
        List<String> agentIds = new ArrayList<>(agentRepository.listBySession(sessionId));

        for (Task task : taskService.listBySession(sessionId)) {
            taskService.delete(task.getId());
        }

        toolCallRepository.delete(sessionId);

        for (String aid : agentIds) {
            memoryService.deleteAgent(aid);
        }
        if (session.getRootAgentId() != null) {
            memoryService.deleteAgent(session.getRootAgentId());
        }

        blackboardService.deleteSession(sessionId);

        // Keep Python parity: only root agent file is explicitly deleted.
        if (session.getRootAgentId() != null) {
            agentRepository.delete(session.getRootAgentId());
        }

        try {
            HitlStore.getInstance().clear(sessionId);
            EventStore.getInstance().delete(sessionId);
        } catch (Exception ignored) {
        }

        sessionService.delete(sessionId);
    }

    public void checkTokenBudget(Session session) {
        if (session.getTokenUsed() >= session.getTokenBudget()) {
            throw new AppException(ErrorCode.TOKEN_BUDGET_EXCEEDED,
                "Session " + session.getId() + " token budget exhausted (" + session.getTokenUsed() + "/"
                    + session.getTokenBudget() + ")");
        }
    }

    private Agent buildRootAgent(Session session, AgentTemplate template, String llmName, String llmModel) {
        Instant now = Instant.now();
        List<String> actToolList = template == null ? new ArrayList<>() : safeList(template.getActToolList());
        List<String> observeToolList = template == null ? new ArrayList<>() : safeList(template.getObserveToolList());
        String soulPath = template == null ? null : blankToNull(template.getSourceDir());

        Agent agent = new Agent(newAgentId(), session.getId(), session.getTemplateId(), "root", AgentStatus.IDLE,
            actToolList, observeToolList, new ArrayList<>(), soulPath, new LoopGuard(0, session.getRootMaxTurns(), 50),
            (llmName == null || llmName.isBlank()) ? defaultLlmName : llmName,
            llmModel == null ? "" : llmModel, now, now);
        if (template != null) {
            applyTemplateToAgent(template, agent);
        }
        agent.setSettings(Map.of());
        agent.setHasSpawnPermission(true);
        agent.setSpawnDepth(0);
        agent.setInheritMemory(true);
        return agent;
    }

    private void applyTemplateToAgent(AgentTemplate template, Agent agent) {
        AgentDefContent content = templateRegistry == null ? null : templateRegistry.loadContent(template.getName());
        agent.setSoulMd(content == null ? "" : defaultString(content.getSoulMd()));
        agent.setRoleMd(content == null ? "" : defaultString(content.getRoleMd()));
        agent.setActToolList(template.getActToolList());
        agent.setObserveToolList(template.getObserveToolList());
        agent.setMcpActServers(template.getMcpActServers());
        agent.setMcpObserveServers(template.getMcpObserveServers());
        agent.setSkillList(new ArrayList<>());
    }

    private void createInitialTask(String sessionId, String creatorAgentId, String userPrompt,
        InitialTaskConfig initialTask) {
        boolean useSubagent = initialTask != null && initialTask.isUseSubagent();
        String title = initialTask != null && initialTask.getTitle() != null ? initialTask.getTitle() : "";
        String description = initialTask != null && initialTask.getDescription() != null
            ? initialTask.getDescription()
            : "";

        Map<String, Object> settings = new LinkedHashMap<>();
        settings.put("use_subagent", useSubagent);
        settings.put("inherit_memory", true);
        if (useSubagent) {
            String templateName = initialTask != null && initialTask.getSubagentTemplate() != null
                && !initialTask.getSubagentTemplate().isBlank()
                ? initialTask.getSubagentTemplate()
                : defaultTemplateName;
            settings.put("subagent_template", templateName);
        }

        Task initial = taskService.create(sessionId, creatorAgentId, creatorAgentId,
            userPrompt, title, description, settings);

        if ((title.isBlank() || description.isBlank()) && lifecycleManager != null) {
            Map<String, Object> metadataSettings = new LinkedHashMap<>();
            metadataSettings.put("subagent_template", "metadata_filler");
            metadataSettings.put("target_task_id", initial.getId());
            metadataSettings.put("_daemon", true);
            Task metaTask = taskService.create(
                sessionId,
                creatorAgentId,
                creatorAgentId,
                userPrompt,
                "Update task meta data details",
                "Summarize the user prompt (" + userPrompt + ") and fill in the task title and description accordingly",
                metadataSettings
            );
            lifecycleManager.spawnDaemonTask(sessionId, creatorAgentId, metaTask.getId());
        }
    }

    private List<String> safeList(List<String> values) {
        return values == null ? new ArrayList<>() : new ArrayList<>(values);
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    private String defaultString(String value) {
        return value == null ? "" : value;
    }

    private String newAgentId() {
        return IdUtils.newAgentId();
    }

    public record CreateSessionResult(Session session, String agentId) {}
}
