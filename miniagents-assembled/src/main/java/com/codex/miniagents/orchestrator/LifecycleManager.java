package com.codex.miniagents.orchestrator;

import com.codex.miniagents.agenttemplate.AgentTemplateRegistry;
import com.codex.miniagents.agenttemplate.definition.AgentDefContent;
import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.event.EventBus;
import com.codex.miniagents.domain.memory.model.MemoryItem;
import com.codex.miniagents.domain.memory.model.MemorySummary;
import com.codex.miniagents.domain.memory.service.MemoryService;
import com.codex.miniagents.domain.model.AgentTemplate;
import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.agent.AgentStatus;
import com.codex.miniagents.domain.model.agent.LoopGuard;
import com.codex.miniagents.domain.model.session.Session;
import com.codex.miniagents.domain.model.session.SessionStatus;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.task.TaskStatus;
import com.codex.miniagents.domain.service.AgentTemplateService;
import com.codex.miniagents.domain.service.SessionService;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.infrastructure.executor.AgentLoopExecutor;
import com.codex.miniagents.infrastructure.storage.repository.AgentRepository;
import com.codex.miniagents.runtime.AgentLoop;
import com.codex.miniagents.utils.IdUtils;

import lombok.extern.slf4j.Slf4j;

import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.locks.ReentrantLock;

@Slf4j
@Component
public class LifecycleManager {
    private static final String AGENT_FINISHED = "AGENT_FINISHED";
    private static final String AGENT_FAILED = "AGENT_FAILED";
    private static final String LIFECYCLE_AGENT_SCHEDULED = "LIFECYCLE_AGENT_SCHEDULED";
    private static final String LIFECYCLE_AGENT_RECYCLED = "LIFECYCLE_AGENT_RECYCLED";
    private static final String SPAWN_REJECTED = "SPAWN_REJECTED";
    private static final int DEFAULT_SUB_AGENT_MAX_TURNS = 10;

    private final SessionService sessionService;
    private final TaskService taskService;
    private final TaskManager taskManager;
    private final AgentRepository agentRepository;
    private final AgentTemplateService templateService;
    private final AgentTemplateRegistry templateRegistry;
    private final MemoryService memoryService;
    private final MiniAgentsProperties properties;
    private final AgentLoopExecutor agentLoopExecutor;
    private final AgentLoop agentLoop;
    private final EventBus eventBus;
    private final int maxConcurrentAgents;
    private final int maxConcurrentTasks;
    private final int maxSpawnDepth;
    private final int maxRetries;

    private final Map<String, SessionState> states = new ConcurrentHashMap<>();
    private final Map<String, ReentrantLock> locks = new ConcurrentHashMap<>();
    private final Map<String, List<PendingAutoSpawn>> pendingAutoSpawns = new ConcurrentHashMap<>();

    public LifecycleManager(SessionService sessionService, TaskService taskService, TaskManager taskManager,
        AgentRepository agentRepository, AgentTemplateService templateService, MemoryService memoryService,
        AgentTemplateRegistry templateRegistry, MiniAgentsProperties properties, AgentLoopExecutor agentLoopExecutor,
        AgentLoop agentLoop, EventBus eventBus) {
        this.sessionService = sessionService;
        this.taskService = taskService;
        this.taskManager = taskManager;
        this.agentRepository = agentRepository;
        this.templateService = templateService;
        this.memoryService = memoryService;
        this.templateRegistry = templateRegistry;
        this.properties = properties;
        this.agentLoopExecutor = agentLoopExecutor;
        this.agentLoop = agentLoop;
        this.eventBus = eventBus;
        this.maxConcurrentAgents = properties.getMaxConcurrentAgents();
        this.maxConcurrentTasks = properties.getMaxConcurrentTasks();
        this.maxSpawnDepth = properties.getMaxSpawnDepth();
        this.maxRetries = properties.getMaxRetries();

        eventBus.subscribe(AGENT_FINISHED, this::onAgentFinished);
        eventBus.subscribe(AGENT_FAILED, this::onAgentFailed);
    }

    public void initSession(String sessionId) {
        states.put(sessionId, new SessionState(
            sessionId,
            maxConcurrentAgents,
            maxConcurrentTasks,
            maxRetries
        ));
        locks.put(sessionId, new ReentrantLock());
        pendingAutoSpawns.remove(sessionId);
    }

    public void scheduleInitialTask(String sessionId, String rootAgentId, String taskId) {
        SessionState state = states.get(sessionId);
        if (state == null) {
            log.error("LifecycleManager: no state for session {}", sessionId);
            return;
        }

        Task task;
        try {
            task = taskService.get(taskId);
        } catch (Exception e) {
            log.error("LifecycleManager: cannot load initial task {}", taskId, e);
            return;
        }

        ReentrantLock lock = locks.computeIfAbsent(sessionId, sid -> new ReentrantLock());
        lock.lock();
        try {
            state.rootAgentId = rootAgentId;
            AgentMeta root = state.agentRegistry.get(rootAgentId);
            if (root == null) {
                state.agentRegistry.put(rootAgentId, new AgentMeta(
                    rootAgentId,
                    asBoolean(taskSettings(task).get("use_subagent"), false) ? null : taskId,
                    0,
                    "RUNNING"
                ));
                state.concurrentAgents += 1;
            } else {
                root.taskId = asBoolean(taskSettings(task).get("use_subagent"), false) ? null : taskId;
                root.status = "RUNNING";
            }
        } finally {
            lock.unlock();
        }

        if (asBoolean(taskSettings(task).get("use_subagent"), false)) {
            autoSpawnForTask(sessionId, rootAgentId, taskId);
        } else {
            scheduleTask(sessionId, rootAgentId, taskId);
        }
    }

    public void scheduleTask(String sessionId, String agentId, String taskId) {
        agentLoopExecutor.submit(sessionId, agentId, () -> runTaskSafe(sessionId, agentId, taskId));
    }

    public void spawnDaemonTask(String sessionId, String parentAgentId, String taskId) {
        try {
            Task task = taskService.get(taskId);
            String templateName = task.getSettings() == null ? "" : String.valueOf(task.getSettings().getOrDefault("subagent_template", ""));
            String subAgentId = instantiateSubAgent(
                sessionId,
                taskId,
                parentAgentId,
                1,
                false,
                templateName
            );
            task.setAssignedAgentId(subAgentId);
            taskService.save(task);
            try {
                taskService.transition(taskId, TaskStatus.ACTIVE);
            } catch (Exception e) {
                log.warn("LifecycleManager: spawnDaemonTask could not pre-activate task {}", taskId, e);
            }
            log.info("LifecycleManager: spawning daemon sub-agent {} for task {}", subAgentId, taskId);
            scheduleTask(sessionId, subAgentId, taskId);
        } catch (Exception e) {
            log.error("LifecycleManager: spawnDaemonTask failed for task {}", taskId, e);
        }
    }

    public void cleanupSession(String sessionId) {
        states.remove(sessionId);
        locks.remove(sessionId);
        pendingAutoSpawns.remove(sessionId);
    }

    private void runTaskSafe(String sessionId, String agentId, String taskId) {
        try {
            try {
                Session session = sessionService.get(sessionId);
                if (session.getStatus() == SessionStatus.QUEUED) {
                    sessionService.transition(sessionId, SessionStatus.RUNNING);
                }
            } catch (Exception ignored) {
                // best effort
            }

            try {
                Task task = taskService.get(taskId);
                if (task.getStatus() == TaskStatus.PENDING) {
                    taskService.transition(taskId, TaskStatus.ACTIVE);
                }
            } catch (Exception e) {
                log.warn("LifecycleManager: failed to activate task {}", taskId, e);
                return;
            }

            agentLoop.run(sessionId, agentId, taskId);
            taskManager.recordSuccess(sessionId);
            eventBus.publish(AGENT_FINISHED,
                Map.of("session_id", sessionId, "agent_id", agentId, "task_id", taskId, "success", true));
        } catch (Exception e) {
            log.error("LifecycleManager: agent {} failed on task {}", agentId, taskId, e);
            taskManager.recordFailure(sessionId);
            try {
                taskService.fail(taskId, String.valueOf(e));
            } catch (Exception ignored) {
                // best effort
            }
            eventBus.publish(AGENT_FAILED,
                Map.of("session_id", sessionId, "agent_id", agentId, "task_id", taskId, "error", String.valueOf(e)));
        }
    }

    private void onAgentFinished(String eventType, Map<String, Object> payload) {
        String agentId = String.valueOf(payload.getOrDefault("agent_id", ""));
        String sessionId = String.valueOf(payload.getOrDefault("session_id", ""));
        SessionState state = states.get(sessionId);
        if (state == null || !state.agentRegistry.containsKey(agentId)) {
            return;
        }

        AgentFinishedDecision decision = new AgentFinishedDecision();

        ReentrantLock lock = locks.computeIfAbsent(sessionId, sid -> new ReentrantLock());
        lock.lock();
        try {
            AgentMeta meta = state.agentRegistry.get(agentId);
            if (meta == null) {
                return;
            }

            if (meta.spawnDepth == 0) {
                decision = handleRootAgentFinished(sessionId, agentId, state, meta, payload);
            } else {
                decision = handleSubAgentFinished(sessionId, agentId, state, meta);
            }
        } finally {
            lock.unlock();
        }

        if (decision.nextTaskToSchedule != null) {
            String scheduleAgentId = decision.rootResumeAgentId == null ? agentId : decision.rootResumeAgentId;
            scheduleTask(sessionId, scheduleAgentId, decision.nextTaskToSchedule.getId());
        }

        List<PendingAutoSpawn> autoSpawns = pendingAutoSpawns.remove(sessionId);
        if (autoSpawns == null) {
            autoSpawns = List.of();
        }
        for (PendingAutoSpawn item : autoSpawns) {
            autoSpawnForTask(sessionId, item.rootAgentId, item.taskId);
        }
    }

    private AgentFinishedDecision handleRootAgentFinished(String sessionId, String agentId, SessionState state,
        AgentMeta meta, Map<String, Object> payload) {
        SessionStatus sessionStatus;
        try {
            sessionStatus = sessionService.get(sessionId).getStatus();
        } catch (Exception e) {
            sessionStatus = SessionStatus.FAILED;
        }

        if (sessionStatus != SessionStatus.RUNNING) {
            recycleAgent(sessionId, agentId, state);
            return new AgentFinishedDecision();
        }

        String finishedTaskId = String.valueOf(payload.getOrDefault("task_id", ""));
        if (!finishedTaskId.isBlank()) {
            tryResumeParentTask(sessionId, finishedTaskId);
        }

        List<Task> pending = taskService.listPending(sessionId);
        if (pending.isEmpty()) {
            log.info("Session {}: no pending tasks, marking SUCCEEDED", sessionId);
            try {
                sessionService.transition(sessionId, SessionStatus.SUCCEEDED);
            } catch (Exception e) {
                log.error("LifecycleManager: failed to transition session {} to SUCCEEDED", sessionId, e);
            }
            recycleAgent(sessionId, agentId, state);
            return new AgentFinishedDecision();
        }

        Task nextTaskToSchedule = pending.get(0);
        meta.taskId = nextTaskToSchedule.getId();
        if (asBoolean(taskSettings(nextTaskToSchedule).get("use_subagent"), false)) {
            String rejectReason = checkSpawnPermission(state, agentId, List.of(
                new SpawnPlanItem(nextTaskToSchedule.getTitle(), nextTaskToSchedule.getDescription())
            ));
            if (!rejectReason.isBlank()) {
                eventBus.publish(SPAWN_REJECTED, Map.of(
                    "session_id", sessionId,
                    "agent_id", agentId,
                    "task_id", nextTaskToSchedule.getId(),
                    "reason", rejectReason
                ));
                log.warn("LifecycleManager: auto-spawn rejected for task {} ({}), fallback inline",
                    nextTaskToSchedule.getId(), rejectReason);
            } else {
                pendingAutoSpawns.computeIfAbsent(sessionId, sid -> new ArrayList<>())
                    .add(new PendingAutoSpawn(agentId, nextTaskToSchedule.getId()));
                meta.taskId = null;
                nextTaskToSchedule = null;
            }
        }

        return new AgentFinishedDecision(nextTaskToSchedule, null);
    }

    private AgentFinishedDecision handleSubAgentFinished(String sessionId, String agentId, SessionState state,
        AgentMeta meta) {
        String finishedTaskId = meta.taskId;
        state.agentRegistry.remove(agentId);
        state.concurrentAgents -= 1;
        if (finishedTaskId != null) {
            state.concurrentTasks -= 1;
        }

        log.info("LM: sub-agent {} finished (task={})", agentId, finishedTaskId);
        eventBus.publish(LIFECYCLE_AGENT_RECYCLED, Map.of("session_id", sessionId, "agent_id", agentId));

        String rootId = state.rootAgentId;
        AgentMeta rootMeta = state.agentRegistry.get(rootId);
        if (rootMeta == null) {
            return new AgentFinishedDecision();
        }

        List<Task> pending = taskService.listPending(sessionId);
        if (pending.isEmpty()) {
            log.info("Session {}: no pending tasks after sub-agent, marking SUCCEEDED", sessionId);
            try {
                sessionService.transition(sessionId, SessionStatus.SUCCEEDED);
            } catch (Exception e) {
                log.error("LifecycleManager: failed to transition session {} to SUCCEEDED", sessionId, e);
            }
            return new AgentFinishedDecision();
        }

        Task nextTaskToSchedule = pending.get(0);
        rootMeta.taskId = nextTaskToSchedule.getId();
        return new AgentFinishedDecision(nextTaskToSchedule, rootId);
    }

    private void tryResumeParentTask(String sessionId, String finishedTaskId) {
        try {
            Task finishedTask = taskService.get(finishedTaskId);
            if (finishedTask.getParentTaskId() == null || finishedTask.getParentTaskId().isBlank()) {
                return;
            }
            Task parent = taskService.get(finishedTask.getParentTaskId());
            if (parent.getStatus() != TaskStatus.SUSPENDED) {
                return;
            }
            List<Task> allTasks = taskService.listBySession(sessionId);
            List<Task> children = allTasks.stream()
                .filter(t -> t != null && parent.getId().equals(t.getParentTaskId()))
                .toList();
            boolean allChildrenTerminal = !children.isEmpty()
                && children.stream().allMatch(t -> t.getStatus() != null && t.getStatus().isTerminal());
            if (allChildrenTerminal) {
                taskService.resume(parent.getId());
            }
        } catch (Exception e) {
            log.error("LM: failed to resume parent task for finished task {}", finishedTaskId, e);
        }
    }

    private void recycleAgent(String sessionId, String agentId, SessionState state) {
        state.agentRegistry.remove(agentId);
        state.concurrentAgents -= 1;
        eventBus.publish(LIFECYCLE_AGENT_RECYCLED, Map.of("session_id", sessionId, "agent_id", agentId));
    }

    private void onAgentFailed(String eventType, Map<String, Object> payload) {
        String agentId = String.valueOf(payload.getOrDefault("agent_id", ""));
        String sessionId = String.valueOf(payload.getOrDefault("session_id", ""));
        SessionState state = states.get(sessionId);
        if (state == null || !state.agentRegistry.containsKey(agentId)) {
            return;
        }

        String resumeRootAgentId = null;
        Task resumeRootTask = null;

        ReentrantLock lock = locks.computeIfAbsent(sessionId, sid -> new ReentrantLock());
        lock.lock();
        try {
            AgentMeta meta = state.agentRegistry.remove(agentId);
            if (meta == null) {
                return;
            }

            state.concurrentAgents -= 1;
            if (meta.taskId != null) {
                state.concurrentTasks -= 1;
            }

            if (meta.spawnDepth == 0) {
                String failedTaskId = meta.taskId;
                int retryCount = failedTaskId == null ? state.maxRetries
                    : state.retryCounts.getOrDefault(failedTaskId, 0);
                if (failedTaskId != null && retryCount < state.maxRetries) {
                    state.retryCounts.put(failedTaskId, retryCount + 1);
                    log.info("LM: retrying task {} (attempt {}/{}) for session {}",
                        failedTaskId, retryCount + 1, state.maxRetries, sessionId);
                    boolean retryOk = false;
                    try {
                        taskService.retry(failedTaskId);
                        retryOk = true;
                    } catch (Exception e) {
                        log.warn("LM: failed to reset task {} for retry: {}", failedTaskId, e.getMessage());
                        try {
                            sessionService.transition(sessionId, SessionStatus.FAILED);
                        } catch (Exception ignored) {
                            // best effort
                        }
                    }
                    if (retryOk) {
                        resumeRootAgentId = agentId;
                        resumeRootTask = taskService.get(failedTaskId);
                        state.agentRegistry.put(agentId, new AgentMeta(agentId, failedTaskId, 0, "RUNNING"));
                        state.concurrentAgents += 1;
                    }
                } else {
                    try {
                        sessionService.transition(sessionId, SessionStatus.FAILED);
                    } catch (Exception ignored) {
                        // best effort
                    }
                }
            } else {
                String rootId = state.rootAgentId;
                AgentMeta rootMeta = state.agentRegistry.get(rootId);
                if (rootMeta != null) {
                    List<Task> pending = taskService.listPending(sessionId);
                    if (!pending.isEmpty()) {
                        rootMeta.taskId = pending.get(0).getId();
                        resumeRootAgentId = rootId;
                        resumeRootTask = pending.get(0);
                    } else {
                        log.warn("LM: sub-agent {} failed with no pending tasks, marking session {} FAILED",
                            agentId, sessionId);
                        try {
                            sessionService.transition(sessionId, SessionStatus.FAILED);
                        } catch (Exception ignored) {
                            // best effort
                        }
                    }
                }
            }
        } finally {
            lock.unlock();
        }

        if (resumeRootAgentId != null && resumeRootTask != null) {
            scheduleTask(sessionId, resumeRootAgentId, resumeRootTask.getId());
        }
    }

    private void autoSpawnForTask(String sessionId, String rootAgentId, String taskId) {
        SessionState state = states.get(sessionId);
        if (state == null) {
            log.error("LifecycleManager: _autoSpawnForTask no state for session {}", sessionId);
            return;
        }

        ReentrantLock lock = locks.computeIfAbsent(sessionId, sid -> new ReentrantLock());
        String subAgentId;
        lock.lock();
        try {
            Task task = taskService.get(taskId);
            boolean inheritMemory = asBoolean(taskSettings(task).get("inherit_memory"), true);
            String templateName = String.valueOf(taskSettings(task).getOrDefault("subagent_template", ""));
            int spawnDepth = 1;

            subAgentId = instantiateSubAgent(sessionId, taskId, rootAgentId, spawnDepth, inheritMemory, templateName);
            task.setAssignedAgentId(subAgentId);
            taskService.save(task);

            state.agentRegistry.put(subAgentId, new AgentMeta(subAgentId, taskId, spawnDepth, "RUNNING"));
            state.concurrentAgents += 1;
            state.concurrentTasks += 1;

            eventBus.publish(LIFECYCLE_AGENT_SCHEDULED,
                Map.of("session_id", sessionId, "agent_id", subAgentId, "task_id", taskId));
        } finally {
            lock.unlock();
        }

        scheduleTask(sessionId, subAgentId, taskId);
    }

    private String checkSpawnPermission(SessionState state, String requestingAgentId, List<SpawnPlanItem> plan) {
        if (plan == null || plan.isEmpty()) {
            return "Empty spawn plan";
        }
        AgentMeta meta = state.agentRegistry.get(requestingAgentId);
        if (meta == null) {
            return "Requesting agent not in registry";
        }
        if (meta.spawnDepth >= maxSpawnDepth) {
            return "Max spawn depth " + maxSpawnDepth + " reached";
        }
        if (state.concurrentAgents + plan.size() > state.maxConcurrentAgents) {
            return "concurrent_agents limit (" + state.maxConcurrentAgents + ") would be exceeded by "
                + plan.size() + " new agents";
        }
        try {
            Session session = sessionService.get(state.sessionId);
            if (session.getTokenUsed() > (session.getTokenBudget() * 0.9)) {
                return "Token budget nearly exhausted (>90%)";
            }
        } catch (Exception ignored) {
            // best effort
        }
        return "";
    }

    private String instantiateSubAgent(String sessionId, String taskId, String parentAgentId, int spawnDepth,
        boolean inheritMemory, String templateName) {
        Agent parent = agentRepository.findById(parentAgentId)
            .orElseThrow(() -> new IllegalArgumentException("Parent agent not found: " + parentAgentId));

        AgentTemplate template = null;
        if (templateName != null && !templateName.isBlank()) {
            template = templateService.getByName(templateName);
            if (template == null) {
                log.warn("LifecycleManager: template '{}' not found, fallback to parent config", templateName);
            }
        }

        Instant now = Instant.now();
        Agent sub = new Agent();
        sub.setId(newAgentId());
        sub.setSessionId(sessionId);
        sub.setTemplateId(template == null ? parent.getTemplateId() : template.getId());
        sub.setName((templateName == null || templateName.isBlank()) ? ("sub-agent-d" + spawnDepth) : ("sub-agent-" + templateName));
        sub.setStatus(AgentStatus.IDLE);
        AgentDefContent content = template == null || templateRegistry == null ? null : templateRegistry.loadContent(template.getName());
        sub.setSoulMd(template == null ? parent.getSoulMd() : (content == null ? "" : defaultString(content.getSoulMd())));
        sub.setRoleMd(template == null ? parent.getRoleMd() : (content == null ? "" : defaultString(content.getRoleMd())));
        sub.setActToolList(template == null ? parent.getActToolList() : template.getActToolList());
        sub.setObserveToolList(template == null ? parent.getObserveToolList() : template.getObserveToolList());
        sub.setMcpActServers(template == null ? parent.getMcpActServers() : template.getMcpActServers());
        sub.setMcpObserveServers(template == null ? parent.getMcpObserveServers() : template.getMcpObserveServers());
        sub.setSkillList(template == null ? parent.getSkillList() : new ArrayList<>());
        sub.setSoulPath(parent.getSoulPath());
        sub.setLoopGuard(new LoopGuard(0, DEFAULT_SUB_AGENT_MAX_TURNS, 50));
        sub.setInheritMemory(inheritMemory);
        sub.setLlmName(isBlank(parent.getLlmName()) ? properties.getAgentDefaultLlmName() : parent.getLlmName());
        sub.setLlmModel(parent.getLlmModel());
        sub.setHasSpawnPermission(false);
        sub.setSpawnDepth(spawnDepth);
        sub.setParentTaskId(taskId);
        sub.setCreatedAt(now);
        sub.setUpdatedAt(now);
        agentRepository.save(sub);

        if (inheritMemory) {
            copyMemory(parentAgentId, sub.getId(), sessionId);
        }
        return sub.getId();
    }

    private void copyMemory(String srcAgentId, String dstAgentId, String sessionId) {
        List<MemoryItem> parentMessages = memoryService.getWindow(srcAgentId, 10_000);
        for (MemoryItem msg : parentMessages) {
            memoryService.appendMessage(
                sessionId,
                dstAgentId,
                msg.getRole(),
                msg.getContent(),
                null,
                msg.getToolCallId(),
                msg.getToolCalls()
            );
        }

        MemorySummary parentSummary = memoryService.getSummary(srcAgentId);
        if (parentSummary != null) {
            MemorySummary childSummary = new MemorySummary();
            childSummary.setSessionId(sessionId);
            childSummary.setAgentId(dstAgentId);
            childSummary.setSummaryText(parentSummary.getSummaryText());
            childSummary.setCoveredUpTo(parentMessages.size());
            childSummary.setCreatedAt(Instant.now().toString());
            memoryService.saveSummary(dstAgentId, childSummary);
        }
    }

    private String newAgentId() {
        return IdUtils.newAgentId();
    }

    private boolean asBoolean(Object value, boolean fallback) {
        if (value == null) {
            return fallback;
        }
        if (value instanceof Boolean b) {
            return b;
        }
        if (value instanceof String s) {
            if ("true".equalsIgnoreCase(s)) {
                return true;
            }
            if ("false".equalsIgnoreCase(s)) {
                return false;
            }
        }
        return fallback;
    }

    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    private String defaultString(String value) {
        return value == null ? "" : value;
    }

    private Map<String, Object> taskSettings(Task task) {
        if (task == null) {
            return Map.of();
        }
        Map<String, Object> settings = task.getSettings();
        return settings == null ? Map.of() : settings;
    }

    private static class SessionState {
        private final String sessionId;
        private String rootAgentId = "";
        private final int maxConcurrentAgents;
        private final int maxConcurrentTasks;
        private final int maxRetries;
        private int concurrentAgents = 0;
        private int concurrentTasks = 0;
        private final Map<String, AgentMeta> agentRegistry = new ConcurrentHashMap<>();
        private final Map<String, Integer> retryCounts = new ConcurrentHashMap<>();

        private SessionState(String sessionId, int maxConcurrentAgents, int maxConcurrentTasks, int maxRetries) {
            this.sessionId = sessionId;
            this.maxConcurrentAgents = maxConcurrentAgents;
            this.maxConcurrentTasks = maxConcurrentTasks;
            this.maxRetries = maxRetries;
        }
    }

    private static class AgentMeta {
        private final String agentId;
        private String taskId;
        private final int spawnDepth;
        private String status;

        private AgentMeta(String agentId, String taskId, int spawnDepth, String status) {
            this.agentId = agentId;
            this.taskId = taskId;
            this.spawnDepth = spawnDepth;
            this.status = status;
        }
    }

    private static class SpawnPlanItem {
        private final String title;
        private final String description;

        private SpawnPlanItem(String title, String description) {
            this.title = title;
            this.description = description;
        }
    }

    private static class AgentFinishedDecision {
        private final Task nextTaskToSchedule;
        private final String rootResumeAgentId;

        private AgentFinishedDecision() {
            this(null, null);
        }

        private AgentFinishedDecision(Task nextTaskToSchedule, String rootResumeAgentId) {
            this.nextTaskToSchedule = nextTaskToSchedule;
            this.rootResumeAgentId = rootResumeAgentId;
        }
    }

    private static class PendingAutoSpawn {
        private final String rootAgentId;
        private final String taskId;

        private PendingAutoSpawn(String rootAgentId, String taskId) {
            this.rootAgentId = rootAgentId;
            this.taskId = taskId;
        }
    }
}
