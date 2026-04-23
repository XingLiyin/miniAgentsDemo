package com.codex.miniagents.runtime;

import com.codex.miniagents.common.SseBus;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.service.SessionService;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.llm.model.LlmTool;
import com.codex.miniagents.runtime.model.PlannedTask;
import com.codex.miniagents.tools.model.ToolDefinition;
import com.codex.miniagents.tools.ToolDefinitionFactory;
import com.codex.miniagents.tools.annotation.ToolParam;
import com.codex.miniagents.tools.annotation.ToolSpec;
import com.codex.miniagents.runtime.model.ControlResult;
import com.codex.miniagents.tools.model.ToolResult;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Component
public class AgentController {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final Logger log = LoggerFactory.getLogger(AgentController.class);

    private final TaskService taskService;
    private final SessionService sessionService;
    private final MiniAgentsProperties properties;
    private final Map<String, ControlToolDef> tools = new LinkedHashMap<>();

    public AgentController(TaskService taskService, SessionService sessionService, MiniAgentsProperties properties) {
        this.taskService = taskService;
        this.sessionService = sessionService;
        this.properties = properties;
        registerDefaults();
    }

    public boolean canHandle(String toolName) {
        return tools.containsKey(toolName);
    }

    public List<LlmTool> getLlmSchemas(String scope) {
        List<LlmTool> schemas = new ArrayList<>();
        for (ControlToolDef def : tools.values()) {
            if (scope == null || scope.equals(def.scope)) {
                schemas.add(def.schema.toLlmTool());
            }
        }
        return schemas;
    }

    public ControlResult dispatch(String toolName, Map<String, Object> args, Agent agent, Task task) {
        ControlToolDef def = tools.get(toolName);
        if (def == null) {
            throw new IllegalArgumentException("Unknown control tool: " + toolName);
        }
        return def.handler.handle(args == null ? Map.of() : args, agent, task);
    }

    private void registerDefaults() {
        register(buildRequestHumanInputTool(), "actor", this::handleRequestHumanInput);
        register(buildUpdateTaskMetadataTool(), "actor", this::handleUpdateTaskMetadata);
        register(buildSubmitPlanTool(), "observer", this::handleSubmitPlan);
        register(buildSubmitTaskAssessmentTool(), "observer", this::handleSubmitTaskAssessment);
        register(buildReplanTool(), "observer", this::handleReplan);
    }

    private void register(ToolDefinition schema, String scope, ControlToolHandler handler) {
        tools.put(schema.getName(), new ControlToolDef(schema, scope, handler));
    }

    private ControlResult handleRequestHumanInput(Map<String, Object> args, Agent agent, Task task) {
        String prompt = stringValue(args.get("prompt"));
        String context = stringValue(args.get("context"));
        String creator = task.getAssignedAgentId() == null ? "" : task.getAssignedAgentId();
        sessionService.transition(task.getSessionId(), com.codex.miniagents.domain.model.session.SessionStatus.WAITING_INPUT);
        try {
            SseBus.getInstance().push(task.getSessionId(), Map.of(
                "type", "message",
                "role", "assistant",
                "content", prompt,
                "created_at", java.time.Instant.now().toString()
            ));
            SseBus.getInstance().push(task.getSessionId(), Map.of(
                "type", "waiting_input",
                "prompt", prompt,
                "input_type", "user_input",
                "task_title", "等待用户输入"
            ));
        } catch (Exception ignored) {
        }

        String answer = HitlStore.getInstance().waitForAnswer(
            task.getSessionId(),
            creator,
            prompt,
            "user_input"
        );
        sessionService.transition(task.getSessionId(), com.codex.miniagents.domain.model.session.SessionStatus.RUNNING);

        return ControlResult.builder()
            .toolResult(ToolResult.builder().content(answer).build())
            .signal(ControlSignal.BLOCK_FOR_INPUT)
            .signalData(buildSignalData("prompt", prompt, "answer", answer))
            .build();
    }

    private ControlResult handleSubmitPlan(Map<String, Object> args, Agent agent, Task task) {
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
        String taskOutcome = stringValue(args.get("task_outcome"));
        if (!"success".equals(taskOutcome) && !"failed".equals(taskOutcome) && !"needs_user_input".equals(taskOutcome)) {
            taskOutcome = "success";
        }
        String taskResult = stringValue(args.get("task_result"));
        if (taskResult.isBlank()) {
            taskResult = summary;
        }
        String turnSummary = stringValue(args.get("summary"));
        if (turnSummary.isBlank()) {
            turnSummary = summary;
        }
        return ControlResult.builder()
            .toolResult(ToolResult.builder().content(summary).build())
            .signal(ControlSignal.NONE)
            .signalData(buildSignalData(
                "task_outcome", taskOutcome,
                "task_result", taskResult,
                "summary", turnSummary,
                "proceed_to_review", false
            ))
            .build();
    }

    private ControlResult handleSubmitTaskAssessment(Map<String, Object> args, Agent agent, Task task) {
        String taskOutcome = stringValue(args.get("task_outcome"));
        if (!"success".equals(taskOutcome) && !"failed".equals(taskOutcome) && !"needs_user_input".equals(taskOutcome)) {
            taskOutcome = "failed";
        }

        Map<String, Object> signalData = new LinkedHashMap<>();
        signalData.put("task_outcome", taskOutcome);
        signalData.put("task_result", stringValue(args.get("task_result")));
        signalData.put("summary", stringValue(args.get("summary")));
        signalData.put("proceed_to_review", true);

        return ControlResult.builder()
            .toolResult(ToolResult.builder().content("").build())
            .signal(ControlSignal.NONE)
            .signalData(signalData)
            .build();
    }

    private ControlResult handleReplan(Map<String, Object> args, Agent agent, Task task) {
        String reason = stringValue(args.get("reason"));
        String summary = stringValue(args.get("summary"));
        String userPrompt = stringValue(args.get("user_prompt"));
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
        return ControlResult.builder()
            .toolResult(ToolResult.builder().content("Cancelled " + canceled + " tasks. New plan task created.").build())
            .signal(ControlSignal.NONE)
            .signalData(buildSignalData(
                "task_outcome", "success",
                "task_result", reason,
                "summary", summary,
                "proceed_to_review", false
            ))
            .build();
    }

    private ToolDefinition buildRequestHumanInputTool() {
        return definitionFromMethod("requestHumanInputTool", String.class, String.class);
    }

    private ToolDefinition buildUpdateTaskMetadataTool() {
        return definitionFromMethod("updateTaskMetadataTool", String.class, String.class);
    }

    private ToolDefinition buildSubmitPlanTool() {
        return definitionFromMethod("submitPlanTool", List.class, String.class, String.class, String.class);
    }

    private ToolDefinition buildSubmitTaskAssessmentTool() {
        return definitionFromMethod("submitTaskAssessmentTool", String.class, String.class, String.class);
    }

    private ToolDefinition buildReplanTool() {
        return definitionFromMethod("replanTool", String.class, String.class);
    }

    private ToolDefinition definitionFromMethod(String methodName, Class<?>... parameterTypes) {
        try {
            Method method = AgentController.class.getMethod(methodName, parameterTypes);
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
        description = "Update the title and description of a target task.")
    public ToolResult updateTaskMetadataTool(
        @ToolParam("The task title to save") String title,
        @ToolParam("The task description to save") String description) {
        try {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("title", stringValue(title));
            payload.put("description", stringValue(description));
            return ToolResult.builder().content(OBJECT_MAPPER.writeValueAsString(payload)).build();
        } catch (Exception e) {
            return ToolResult.builder()
                .content("{\"title\":\"" + stringValue(title) + "\",\"description\":\"" + stringValue(description) + "\"}")
                .build();
        }
    }

    @ToolSpec(name = "submit_plan",
        description = "Submit the decomposed task plan. Call exactly once per turn.")
    public ToolResult submitPlanTool(
        @ToolParam(
            "Ordered list of tasks for this turn. Empty list means the goal is already complete. Each task: title, "
                + "description, user_prompt, skill_name, use_subagent, inherit_memory, subagent_template."
        ) List<PlannedTask> tasks,
        @JsonProperty("task_outcome")
        @ToolParam(value = "'success', 'failed', or 'needs_user_input'", required = false)
        String taskOutcome,
        @JsonProperty("task_result")
        @ToolParam(value = "Brief description of what the plan covers, or why planning failed.", required = false)
        String taskResult,
        @JsonProperty("summary")
        @ToolParam(value = "Concise summary of this planning turn (1-3 sentences).", required = false)
        String summary) {
        return ToolResult.builder().content("").build();
    }

    private ControlResult handleUpdateTaskMetadata(Map<String, Object> args, Agent agent, Task task) {
        String targetTaskId = task.getSettings() == null ? "" : stringValue(task.getSettings().get("target_task_id"));
        String title = stringValue(args.get("title")).trim();
        String description = stringValue(args.get("description")).trim();
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
                    SseBus.getInstance().push(target.getSessionId(), java.util.Map.of("type", "task_updated", "task", target));
                } catch (Exception ignored) {
                }
                log.debug("AgentController: updated metadata for task {}: title={}", targetTaskId, title);
            } catch (Exception e) {
                log.warn("AgentController: failed to update metadata for task {}", targetTaskId, e);
            }
        }
        return ControlResult.builder()
            .toolResult(ToolResult.builder().content("ok").build())
            .signal(ControlSignal.TASK_COMPLETE)
            .signalData(buildSignalData(
                "task_outcome", "success",
                "task_result", "metadata updated",
                "summary", ""
            ))
            .build();
    }

    @ToolSpec(name = "submit_task_assessment",
        description = "Submit assessment for the current task.")
    public ToolResult submitTaskAssessmentTool(
        @JsonProperty("task_outcome") @ToolParam("'success', 'failed', or 'needs_user_input'")
        String taskOutcome,
        @JsonProperty("task_result") @ToolParam("What was accomplished, or why the task could not be completed")
        String taskResult,
        @JsonProperty("summary") @ToolParam("Concise summary of this turn (1-3 sentences)")
        String summary) {
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

    private record ControlToolDef(ToolDefinition schema, String scope, ControlToolHandler handler) {}

    @FunctionalInterface
    private interface ControlToolHandler {
        ControlResult handle(Map<String, Object> args, Agent agent, Task task);
    }
}
