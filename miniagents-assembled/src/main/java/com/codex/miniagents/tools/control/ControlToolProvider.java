package com.codex.miniagents.tools.control;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.session.SessionStatus;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.task.TaskStatus;
import com.codex.miniagents.domain.service.SessionService;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.llm.model.LlmTool;
import com.codex.miniagents.runtime.ControlSignal;
import com.codex.miniagents.runtime.HitlStore;
import com.codex.miniagents.common.SseBus;
import com.codex.miniagents.runtime.model.PlannedTask;
import com.codex.miniagents.tools.ToolDefinitionFactory;
import com.codex.miniagents.tools.annotation.ToolParam;
import com.codex.miniagents.tools.annotation.ToolSpec;
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.model.ToolDefinition;
import com.codex.miniagents.tools.model.ToolResult;
import com.codex.miniagents.tools.provider.ToolProvider;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.lang.reflect.Method;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Component
public class ControlToolProvider implements ToolProvider {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final Logger log = LoggerFactory.getLogger(ControlToolProvider.class);

    private final TaskService taskService;
    private final SessionService sessionService;
    private final MiniAgentsProperties properties;
    private final Map<String, ControlToolDef> tools = new LinkedHashMap<>();

    public ControlToolProvider(TaskService taskService, SessionService sessionService, MiniAgentsProperties properties) {
        this.taskService = taskService;
        this.sessionService = sessionService;
        this.properties = properties;
        registerDefaults();
    }

    @Override
    public List<ToolDefinition> listDefinitions() {
        return tools.values().stream().map(ControlToolDef::schema).toList();
    }

    public List<LlmTool> getLlmSchemas(String scope) {
        List<LlmTool> schemas = new ArrayList<>();
        for (ControlToolDef def : tools.values()) {
            if (scope == null || scope.equals(def.scope())) {
                schemas.add(def.schema().toLlmTool());
            }
        }
        return schemas;
    }

    @Override
    public ToolResult call(String toolName, Map<String, Object> arguments, CallContext context) {
        ControlToolDef def = tools.get(toolName);
        if (def == null) {
            throw new IllegalArgumentException("Unknown control tool: " + toolName);
        }
        return def.handler().handle(arguments == null ? Map.of() : arguments, context == null ? CallContext.empty() : context);
    }

    private void registerDefaults() {
        register(buildRequestHumanInputTool(), "actor", this::handleRequestHumanInput);
        register(buildUpdateTaskMetadataTool(), "actor", this::handleUpdateTaskMetadata);
        register(buildSubmitPlanTool(), "observer", this::handleSubmitPlan);
        register(buildSubmitTaskAssessmentTool(), "observer", this::handleSubmitTaskAssessment);
        register(buildReplanTool(), "observer", this::handleReplan);
        register(buildSubmitTaskTool(), "observer", this::handleSubmitTask);
        register(buildSubmitTaskReviewsTool(), "observer", this::handleSubmitTaskReviews);
    }

    private void register(ToolDefinition schema, String scope, ControlToolHandler handler) {
        ToolDefinition mapped = ToolDefinition.builder()
            .name(schema.getName())
            .description(schema.getDescription())
            .inputSchema(schema.getInputSchema())
            .metadata(Map.of("provider", "control", "scope", scope))
            .handler((arguments, context) -> call(schema.getName(), arguments, context))
            .build();
        tools.put(schema.getName(), new ControlToolDef(mapped, scope, handler));
    }

    private ToolResult handleRequestHumanInput(Map<String, Object> args, CallContext ctx) {
        String prompt = stringValue(args.get("prompt"));
        String sessionId = stringValue(ctx.getSessionId());
        Task task = requireTask(ctx);
        String creator = stringValue(ctx.getAgentId());
        if (creator.isBlank()) {
            creator = stringValue(task.getAssignedAgentId());
        }

        sessionService.transition(sessionId, SessionStatus.WAITING_INPUT);
        try {
            SseBus.getInstance().push(sessionId, Map.of(
                "type", "message",
                "role", "assistant",
                "content", prompt,
                "created_at", Instant.now().toString()
            ));
            SseBus.getInstance().push(sessionId, Map.of(
                "type", "waiting_input",
                "prompt", prompt,
                "input_type", "user_input",
                "task_title", "等待用户输入"
            ));
        } catch (Exception ignored) {
        }

        String answer = HitlStore.getInstance().waitForAnswer(
            sessionId,
            creator,
            prompt,
            "user_input"
        );
        sessionService.transition(sessionId, SessionStatus.RUNNING);

        return toolResultWithSignal(
            answer,
            ControlSignal.BLOCK_FOR_INPUT,
            buildSignalData("prompt", prompt, "answer", answer)
        );
    }

    private ToolResult handleSubmitPlan(Map<String, Object> args, CallContext ctx) {
        Task task = requireTask(ctx);
        List<String> titles = new ArrayList<>();
        int created = 0;
        Object tasksObj = args.get("tasks");
        if (tasksObj instanceof List<?> specs) {
            for (Object specObj : specs) {
                if (!(specObj instanceof Map<?, ?> raw)) {
                    continue;
                }
                @SuppressWarnings("unchecked") Map<String, Object> spec = (Map<String, Object>) raw;
                String title = stringValue(spec.get("title"));
                String description = stringValue(spec.get("description"));
                String userPrompt = stringValue(spec.get("user_prompt"));
                String creator = task.getAssignedAgentId();
                Map<String, Object> inputs = new LinkedHashMap<>();
                String skillName = stringValue(spec.get("skill_name"));
                if (!skillName.isBlank()) {
                    inputs.put("skill_name", skillName);
                }
                if (asBoolean(spec.get("use_subagent"), false)) {
                    inputs.put("use_subagent", true);
                    inputs.put("inherit_memory", asBoolean(spec.get("inherit_memory"), true));
                    String template = stringValue(spec.get("subagent_template"));
                    if (!template.isBlank()) {
                        inputs.put("template_name", template);
                    }
                }
                taskService.create(task.getSessionId(), creator, creator, userPrompt, title, description, inputs);
                created += 1;
                titles.add(title);
            }
        }

        boolean done = created == 0;
        String summary = done
            ? "No further tasks needed — goal already achieved."
            : "Planned " + created + " tasks: " + String.join(", ", titles);
        task.setActorDone(true);
        taskService.toBeObserved(task.getId());
        return toolResultWithSignal(
            summary,
            ControlSignal.NONE,
            buildSignalData(
                "task_outcome", "success",
                "task_result", summary,
                "proceed_to_review", false
            )
        );
    }

    private ToolResult handleSubmitTaskAssessment(Map<String, Object> args, CallContext ctx) {
        Task task = requireTask(ctx);
        String taskOutcome = stringValue(args.get("task_outcome"));
        if (!"success".equals(taskOutcome) && !"failed".equals(taskOutcome)
            && !"active".equals(taskOutcome) && !"needs_user_input".equals(taskOutcome)) {
            taskOutcome = "failed";
        }
        String taskResult = stringValue(args.get("task_result"));
        task.setActorOutcome(taskOutcome);
        task.setActorResult(taskResult);
        task.setProceedToReview(true);
        if ("success".equals(taskOutcome)) {
            taskService.finish(task.getId(), taskResult, null);
        } else if ("failed".equals(taskOutcome)) {
            taskService.fail(task.getId(), taskResult);
        } else if ("active".equals(taskOutcome)) {
            taskService.transition(task.getId(), TaskStatus.PENDING);
        } else {
            taskOutcome = confirmWithUser(task, taskResult);
            taskResult = taskService.get(task.getId()).getResult();
            task.setActorOutcome(taskOutcome);
            task.setActorResult(taskResult);
        }
        return toolResultWithSignal(
            "Assessment recorded: outcome=" + taskOutcome + ". " + taskResult,
            ControlSignal.NONE,
            buildSignalData(
                "task_outcome", taskOutcome,
                "task_result", taskResult,
                "proceed_to_review", true
            )
        );
    }

    private ToolResult handleReplan(Map<String, Object> args, CallContext ctx) {
        Task task = requireTask(ctx);
        String reason = stringValue(args.get("reason"));
        String summary = stringValue(args.get("summary"));
        String userPrompt = stringValue(args.get("user_prompt"));
        if (userPrompt.isBlank()) {
            userPrompt = stringValue(task.getUserPrompt());
        }
        int canceled = taskService.cancelPending(task.getSessionId());
        String creator = task.getAssignedAgentId();
        Map<String, Object> inputs = new LinkedHashMap<>();
        inputs.put("use_subagent", true);
        inputs.put("inherit_memory", true);
        inputs.put("subagent_template", properties.getDefaultPlannerTemplateName());
        taskService.create(
            task.getSessionId(),
            creator,
            creator,
            userPrompt,
            "Replan For: " + reason,
            summary + "\n\n请基于最新情况重新制定计划。",
            inputs
        );
        task.setActorOutcome("success");
        task.setActorResult(reason);
        task.setProceedToReview(false);
        taskService.finish(task.getId(), reason);
        return toolResultWithSignal(
            "Cancelled " + canceled + " tasks. New plan task created.",
            ControlSignal.NONE,
            buildSignalData(
                "task_outcome", "success",
                "task_result", reason,
                "summary", summary,
                "proceed_to_review", false
            )
        );
    }

    private ToolResult handleUpdateTaskMetadata(Map<String, Object> args, CallContext ctx) {
        Task task = requireTask(ctx);
        String targetTaskId = task.getSettings() == null ? "" : stringValue(task.getSettings().get("target_task_id"));
        String title = stringValue(args.get("title")).trim();
        String description = stringValue(args.get("description")).trim();
        String sessionGoal = stringValue(args.get("session_goal")).trim();
        if (!targetTaskId.isBlank()) {
            try {
                Task target = taskService.get(targetTaskId);
                if (!title.isBlank()) {
                    target.setTitle(title);
                }
                if (!description.isBlank()) {
                    target.setDescription(description);
                }
                taskService.save(target);
                try {
                    SseBus.getInstance().push(target.getSessionId(), Map.of("type", "task_updated", "task", target));
                } catch (Exception ignored) {
                }
                log.debug("ControlToolProvider: updated metadata for task {}: title={}", targetTaskId, title);
            } catch (Exception e) {
                log.warn("ControlToolProvider: failed to update metadata for task {}", targetTaskId, e);
            }
        }
        if (!sessionGoal.isBlank()) {
            try {
                com.codex.miniagents.domain.model.session.Session session = sessionService.get(task.getSessionId());
                session.setGoal(sessionGoal);
                sessionService.save(session);
                try {
                    SseBus.getInstance().push(task.getSessionId(), Map.of(
                        "type", "session_goal_updated",
                        "session_id", task.getSessionId(),
                        "goal", sessionGoal
                    ));
                } catch (Exception ignored) {
                }
            } catch (Exception e) {
                log.warn("ControlToolProvider: failed to update session goal for session {}", task.getSessionId(), e);
            }
        }
        if (task.getStatus() == TaskStatus.ACTIVE || task.getStatus() == TaskStatus.PENDING) {
            task.setActorDone(true);
            task.setActorOutcome("success");
            task.setActorResult("metadata updated");
            task.setActorSummary("");
            taskService.finish(task.getId(), "metadata updated");
        }
        return toolResultWithSignal(
            "ok",
            ControlSignal.TASK_COMPLETE,
            buildSignalData(
                "task_outcome", "success",
                "task_result", "metadata updated",
                "summary", ""
            )
        );
    }

    private ToolResult handleSubmitTask(Map<String, Object> args, CallContext ctx) {
        Task task = ctx == null ? null : ctx.getTask();
        String creator = task == null ? "" : stringValue(task.getAssignedAgentId());
        Map<String, Object> inputs = new LinkedHashMap<>();
        String skillName = stringValue(args.get("skill_name"));
        if (!skillName.isBlank()) {
            inputs.put("skill_name", skillName);
        }
        if (asBoolean(args.get("use_subagent"), false)) {
            inputs.put("use_subagent", true);
            inputs.put("inherit_memory", asBoolean(args.get("inherit_memory"), true));
        }
        Task created = taskService.create(
            task == null ? "" : task.getSessionId(),
            creator,
            creator,
            stringValue(args.get("user_prompt")),
            stringValue(args.get("title")),
            stringValue(args.get("description")),
            inputs,
            task == null ? null : task.getId()
        );
        if (task != null && task.getStatus() != TaskStatus.SUSPENDED) {
            taskService.transition(task.getId(), TaskStatus.SUSPENDED);
            task.setActorDone(true);
        }
        return ToolResult.builder()
            .content("Task created: id=" + created.getId() + ", title='" + stringValue(created.getTitle()) + "'")
            .build();
    }

    private ToolResult handleSubmitTaskReviews(Map<String, Object> args, CallContext ctx) {
        String sessionId = stringValue(ctx.getSessionId());
        String agentId = stringValue(ctx.getAgentId());
        String currentTaskId = ctx.getTask() == null ? "" : stringValue(ctx.getTask().getId());
        List<Task> sessionTasks = taskService.listBySession(sessionId);

        Map<String, Task> reviewable = new LinkedHashMap<>();
        for (Task t : sessionTasks) {
            if (t == null || currentTaskId.equals(t.getId())) {
                continue;
            }
            if (!agentId.isBlank() && !agentId.equals(stringValue(t.getAssignedAgentId()))) {
                continue;
            }
            TaskStatus status = t.getStatus();
            if (status == TaskStatus.FINISHED || status == TaskStatus.PENDING) {
                reviewable.put(stringValue(t.getTitle()), t);
            }
        }

        Object reviewsObj = args.get("reviews");
        List<String> applied = new ArrayList<>();
        if (reviewsObj instanceof List<?> reviews) {
            for (Object item : reviews) {
                if (!(item instanceof Map<?, ?> rawMap)) {
                    continue;
                }
                String title = stringValue(rawMap.get("task_title"));
                String reviewStatus = stringValue(rawMap.get("review_status"));
                String reasoning = stringValue(rawMap.get("reasoning"));
                if (title.isBlank() || reasoning.isBlank()) {
                    continue;
                }
                Task matched = reviewable.get(title);
                if (matched == null) {
                    log.warn("submit_task_reviews: no reviewable task with title '{}', skipping", title);
                    continue;
                }
                try {
                    if ("reopen".equals(reviewStatus)) {
                        taskService.reopen(matched.getId());
                        log.info("Task {} reopened by observer: {}", matched.getId(), reasoning);
                    } else if ("skip".equals(reviewStatus)) {
                        taskService.finish(matched.getId(),
                            reasoning.isBlank() ? "Completed indirectly per observer." : reasoning);
                        log.info("Task {} skipped by observer: {}", matched.getId(), reasoning);
                    } else if (!"confirmed".equals(reviewStatus)) {
                        continue;
                    }
                    applied.add(title + " -> " + reviewStatus);
                } catch (Exception e) {
                    log.warn("submit_task_reviews: failed to apply review for {}: {}", matched.getId(), e.getMessage());
                }
            }
        }

        return ToolResult.builder()
            .content("Reviews applied: " + (applied.isEmpty() ? "none" : String.join(", ", applied)))
            .build();
    }

    private ToolDefinition buildRequestHumanInputTool() {
        return definitionFromMethod("requestHumanInputTool", String.class, String.class);
    }

    private ToolDefinition buildUpdateTaskMetadataTool() {
        return definitionFromMethod("updateTaskMetadataTool", String.class, String.class, String.class);
    }

    private ToolDefinition buildSubmitPlanTool() {
        return definitionFromMethod("submitPlanTool", List.class);
    }

    private ToolDefinition buildSubmitTaskAssessmentTool() {
        return definitionFromMethod("submitTaskAssessmentTool", String.class, String.class);
    }

    private ToolDefinition buildReplanTool() {
        return definitionFromMethod("replanTool", String.class, String.class);
    }

    private ToolDefinition buildSubmitTaskTool() {
        return definitionFromMethod("submitTaskTool", String.class, String.class, String.class, boolean.class,
            boolean.class, String.class);
    }

    private ToolDefinition buildSubmitTaskReviewsTool() {
        return definitionFromMethod("submitTaskReviewsTool", List.class);
    }

    private ToolDefinition definitionFromMethod(String methodName, Class<?>... parameterTypes) {
        try {
            Method method = ControlToolProvider.class.getMethod(methodName, parameterTypes);
            return ToolDefinitionFactory.fromMethod(this, method);
        } catch (NoSuchMethodException e) {
            throw new IllegalStateException("Missing control tool method: " + methodName, e);
        }
    }

    @ToolSpec(name = "request_human_input",
        description = "Request user input and wait for answer.")
    public ToolResult requestHumanInputTool(
        @ToolParam("The question or instruction to show the user") String prompt,
        @ToolParam(value = "Optional background context for the user", required = false) String context) {
        try {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("prompt", stringValue(prompt));
            payload.put("context", stringValue(context));
            return ToolResult.builder().content(OBJECT_MAPPER.writeValueAsString(payload)).build();
        } catch (Exception e) {
            return ToolResult.builder()
                .content("{\"prompt\":\"" + stringValue(prompt) + "\",\"context\":\"" + stringValue(context) + "\"}")
                .build();
        }
    }

    @ToolSpec(name = "update_task_metadata",
        description = "Update the title and description of a target task, and optionally update the overall session goal.")
    public ToolResult updateTaskMetadataTool(
        @ToolParam("The task title to save") String title,
        @ToolParam("The task description to save") String description,
        @JsonProperty("session_goal")
        @ToolParam(value = "Overall session goal (<=60 chars). Fill only on first setup or when user direction fundamentally changes; otherwise leave empty", required = false)
        String sessionGoal) {
        try {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("title", stringValue(title));
            payload.put("description", stringValue(description));
            payload.put("session_goal", stringValue(sessionGoal));
            return ToolResult.builder().content(OBJECT_MAPPER.writeValueAsString(payload)).build();
        } catch (Exception e) {
            return ToolResult.builder()
                .content("{\"title\":\"" + stringValue(title) + "\",\"description\":\"" + stringValue(description)
                    + "\",\"session_goal\":\"" + stringValue(sessionGoal) + "\"}")
                .build();
        }
    }

    @ToolSpec(name = "submit_plan",
        description = "Submit the decomposed task plan. Call exactly once per turn.")
    public ToolResult submitPlanTool(
        @ToolParam(
            "Ordered list of tasks for this turn. Empty list means the goal is already complete. Each task: title, "
                + "description, user_prompt, skill_name, use_subagent, inherit_memory, subagent_template."
        ) List<PlannedTask> tasks) {
        return ToolResult.builder().content("").build();
    }

    @ToolSpec(name = "submit_task_assessment",
        description = "Submit assessment for the current task.")
    public ToolResult submitTaskAssessmentTool(
        @JsonProperty("task_outcome") @ToolParam("'success', 'failed', 'active', or 'needs_user_input'")
        String taskOutcome,
        @JsonProperty("task_result") @ToolParam("What was accomplished, progress made, or why the task could not be completed")
        String taskResult) {
        return ToolResult.builder().content("").build();
    }

    @ToolSpec(name = "replan",
        description = "Discard pending tasks and create a fresh plan task.")
    public ToolResult replanTool(
        @ToolParam("Why the current plan must be discarded and rebuilt from scratch") String reason,
        @ToolParam(value = "Concise summary of what was accomplished before this replan (1-3 sentences)", required = false)
        String summary) {
        try {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("reason", stringValue(reason));
            payload.put("summary", stringValue(summary));
            return ToolResult.builder().content(OBJECT_MAPPER.writeValueAsString(payload)).build();
        } catch (Exception e) {
            return ToolResult.builder()
                .content("{\"reason\":\"" + stringValue(reason) + "\",\"summary\":\"" + stringValue(summary) + "\"}")
                .build();
        }
    }

    @ToolSpec(name = "submit_task",
        description = "Create a single new task in the current session. The current task continues running.")
    public ToolResult submitTaskTool(
        @ToolParam("Short imperative title for the task (<=20 chars)") String title,
        @ToolParam("WHAT to achieve - not HOW, no tool names or arguments (<=80 chars)") String description,
        @ToolParam(value = "Skill to assign to the task, or empty string if none", required = false) String skillName,
        @ToolParam(value = "True if the task should run in an independent sub-agent", required = false)
        boolean useSubagent,
        @ToolParam(value = "True (default) for sub-agents that need session history", required = false)
        boolean inheritMemory,
        @ToolParam(value = "The user prompt that triggered this task, or empty", required = false) String userPrompt) {
        return ToolResult.builder().content("").build();
    }

    @ToolSpec(name = "submit_task_reviews",
        description = "Submit your review of all FINISHED and PENDING tasks in the session.")
    public ToolResult submitTaskReviewsTool(
        @ToolParam(
            "Review entries for FINISHED and PENDING tasks in the session. Each entry: task_title, current_status, "
                + "review_status ('confirmed' | 'reopen' | 'skip'), reasoning."
        ) List<Map<String, Object>> reviews) {
        return ToolResult.builder().content("").build();
    }

    private ToolResult toolResultWithSignal(String content, ControlSignal signal, Map<String, Object> signalData) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("control_signal", signal.name());
        metadata.put("signal_data", signalData);
        return ToolResult.builder()
            .content(content)
            .metadata(metadata)
            .build();
    }

    private String confirmWithUser(Task task, String taskResult) {
        String prompt = "任务「" + stringValue(task.getTitle()) + "」已执行，但系统无法自动判定完成状态。\n\n"
            + "执行结果：\n" + (taskResult == null || taskResult.isBlank() ? "（无输出）" : taskResult) + "\n\n"
            + "请确认任务是否完成，或补充说明以便 Agent 重新规划。";
        sessionService.transition(task.getSessionId(), SessionStatus.WAITING_INPUT);
        try {
            SseBus.getInstance().push(task.getSessionId(), Map.of(
                "type", "message",
                "role", "assistant",
                "content", prompt,
                "created_at", Instant.now().toString()
            ));
            SseBus.getInstance().push(task.getSessionId(), Map.of(
                "type", "waiting_input",
                "prompt", prompt,
                "input_type", "task_completion_confirm",
                "task_title", "请确认任务完成状态"
            ));
        } catch (Exception ignored) {
        }

        String answer = HitlStore.getInstance().waitForAnswer(
            task.getSessionId(),
            stringValue(task.getAssignedAgentId()),
            prompt,
            "task_completion_confirm"
        );
        sessionService.transition(task.getSessionId(), SessionStatus.RUNNING);

        String confirmPrefix = "用户已确认任务完成";
        if (answer != null && answer.startsWith(confirmPrefix)) {
            taskService.finish(task.getId(), taskResult, null);
            return "success";
        }

        String prefix = "用户表示任务未完成，请重试。用户补充说明：";
        String feedback = answer != null && answer.startsWith(prefix) ? answer.substring(prefix.length()) : answer;
        String error = (feedback == null || feedback.isBlank()) ? "用户确认任务未完成" : feedback;
        taskService.fail(task.getId(), error);
        return "failed";
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

    private String stringValue(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private Map<String, Object> buildSignalData(Object... kvs) {
        Map<String, Object> data = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kvs.length; i += 2) {
            String key = String.valueOf(kvs[i]);
            Object value = kvs[i + 1];
            data.put(key, value);
        }
        return data;
    }

    private Task requireTask(CallContext ctx) {
        if (ctx == null || ctx.getTask() == null) {
            throw new IllegalArgumentException("Missing task in CallContext");
        }
        return ctx.getTask();
    }

    private record ControlToolDef(ToolDefinition schema, String scope, ControlToolHandler handler) {}

    @FunctionalInterface
    private interface ControlToolHandler {
        ToolResult handle(Map<String, Object> args, CallContext context);
    }
}
