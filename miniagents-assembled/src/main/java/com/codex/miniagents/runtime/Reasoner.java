package com.codex.miniagents.runtime;

import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.blackboard.BlackboardEntry;
import com.codex.miniagents.domain.model.session.Session;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.service.BlackboardService;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.domain.memory.model.MemoryItem;
import com.codex.miniagents.domain.memory.service.MemoryService;
import com.codex.miniagents.dto.response.SearchResult;
import com.codex.miniagents.llm.model.LlmTool;
import com.codex.miniagents.skills.SkillRegistry;
import com.codex.miniagents.skills.SkillStoreClient;
import com.codex.miniagents.tools.ToolStoreClient;
import com.codex.miniagents.tools.control.ControlToolProvider;
import com.codex.miniagents.tools.registry.ToolRegistry;
import com.codex.miniagents.runtime.model.ContextResource;
import com.codex.miniagents.runtime.model.ReasoningContext;
import com.codex.miniagents.runtime.model.SkillMeta;
import com.codex.miniagents.utils.TokenUtils;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

@Component
public class Reasoner {
    private final MemoryService memoryService;

    private final BlackboardService blackboardService;

    private final TaskService taskService;

    private final ToolRegistry toolRegistry;
    private final ToolStoreClient toolStoreClient;

    private final SkillRegistry skillRegistry;
    private final SkillStoreClient skillStoreClient;
    private final ControlToolProvider controlToolProvider;

    public Reasoner(MemoryService memoryService, BlackboardService blackboardService, TaskService taskService, ToolRegistry toolRegistry,
        ToolStoreClient toolStoreClient, SkillRegistry skillRegistry, SkillStoreClient skillStoreClient,
        ControlToolProvider controlToolProvider) {
        this.memoryService = memoryService;
        this.blackboardService = blackboardService;
        this.taskService = taskService;
        this.toolRegistry = toolRegistry;
        this.toolStoreClient = toolStoreClient;
        this.skillRegistry = skillRegistry;
        this.skillStoreClient = skillStoreClient;
        this.controlToolProvider = controlToolProvider;
    }

    public ReasoningContext reason(Session session, Agent agent, Task task) {
        List<String> snippets = new ArrayList<>(blackboardService.pull(session.getId(), "_root", agent.getId())
            .stream()
            .map(BlackboardEntry::getContent)
            .collect(Collectors.toList()));
        if (task != null) {
            for (Task child : listChildren(session.getId(), task.getId())) {
                blackboardService.pull(session.getId(), child.getId(), agent.getId())
                    .stream()
                    .map(BlackboardEntry::getContent)
                    .forEach(snippets::add);
            }
        }
        var summary = agent.isInheritMemory() ? memoryService.getSummary(agent.getId()) : null;
        List<MemoryItem> recentMessages = agent.isInheritMemory()
            ? memoryService.getWindow(agent.getId(), null)
            : new ArrayList<>();
        String summaryText = summary == null ? "" : summary.getSummaryText();
        String sessionGoal = defaultString(session.getGoal());
        List<LlmTool> relevantTools = retrieveActTools(sessionGoal, agent);
        List<SkillMeta> relevantSkills = retrieveSkills(sessionGoal, agent);
        String skillInstructions = extractSkillInstructions(task);
        List<ContextResource> actorResources = buildActorResources(agent, relevantTools, relevantSkills);
        List<ContextResource> observerResources = buildObserverResources(agent);
        String sample = sessionGoal
            + summaryText
            + recentMessages.stream().map(m -> m.getContent() == null ? "" : m.getContent()).collect(Collectors.joining(" "))
            + String.join(" ", snippets);

        return ReasoningContext.builder()
            .goal(sessionGoal)
            .recentMessages(toMessageMaps(recentMessages))
            .summaryText(summaryText)
            .blackboardSnippets(snippets)
            .soul(defaultString(agent.getSoulMd()))
            .role(defaultString(agent.getRoleMd()))
            .skillInstructions(skillInstructions)
            .actorResources(actorResources)
            .observerResources(observerResources)
            .currentTask(task)
            .tokenEstimate(TokenUtils.estimateTokens(sample))
            .build();
    }

    private List<LlmTool> retrieveActTools(String goal, Agent agent) {
        List<String> allowedNames = resolveActToolNames(agent);
        if (allowedNames.isEmpty()) {
            return List.of();
        }
        try {
            if (toolStoreClient.isEnabled()) {
                List<SearchResult> results = toolStoreClient.search(goal == null ? "" : goal, 10);
                if (results != null && !results.isEmpty()) {
                    Set<String> allowed = Set.copyOf(allowedNames);
                    List<String> names = results.stream()
                        .map(SearchResult::getName)
                        .filter(allowed::contains)
                        .toList();
                    if (!names.isEmpty()) {
                        return toolRegistry.toLlmTools(names);
                    }
                }
            }
        } catch (Exception ignored) {
            // fallback to full list
        }
        return toolRegistry.toLlmTools(allowedNames);
    }

    private List<SkillMeta> retrieveSkills(String goal, Agent agent) {
        List<SkillMeta> results = new ArrayList<>();
        skillRegistry.listAll().forEach(meta -> results.add(
            SkillMeta.builder().name(meta.getName()).description(meta.getDescription()).build()
        ));
        return results;
    }

    private String extractSkillInstructions(Task task) {
        if (task == null) {
            return "";
        }
        Map<String, Object> settings = task.getSettings();
        if (settings == null) {
            return "";
        }
        Object skillNameObj = settings.get("skill_name");
        if (!(skillNameObj instanceof String skillName) || skillName.isBlank()) {
            return "";
        }
        var definition = skillRegistry.loadDefinition(skillName);
        return definition == null ? "" : defaultString(definition.getInstructions());
    }

    private List<ContextResource> buildActorResources(Agent agent, List<LlmTool> tools, List<SkillMeta> skills) {
        List<ContextResource> resources = new ArrayList<>();
        Set<String> allowed = resolveActToolNames(agent).isEmpty() ? Set.of() : Set.copyOf(resolveActToolNames(agent));
        for (SkillMeta skill : skills) {
            resources.add(ContextResource.builder()
                .name(skill.getName())
                .description(defaultString(skill.getDescription()))
                .kind("skill")
                .build());
        }
        for (LlmTool tool : tools) {
            resources.add(ContextResource.builder()
                .name(tool.getName())
                .description(defaultString(tool.getDescription()))
                .kind("tool")
                .llmTool(tool)
                .build());
        }
        for (LlmTool tool : controlToolProvider.getLlmSchemas("actor")) {
            if (!allowed.isEmpty() && !allowed.contains(tool.getName())) {
                continue;
            }
            resources.add(ContextResource.builder()
                .name(tool.getName())
                .description(defaultString(tool.getDescription()))
                .kind("tool")
                .llmTool(tool)
                .build());
        }
        return resources;
    }

    private List<ContextResource> buildObserverResources(Agent agent) {
        List<LlmTool> candidates = new ArrayList<>(controlToolProvider.getLlmSchemas("observer"));
        Set<String> allowed = resolveObserveToolNames(agent).isEmpty() ? Set.of() : Set.copyOf(resolveObserveToolNames(agent));
        if (allowed.isEmpty()) {
            return List.of();
        }
        List<ContextResource> filtered = new ArrayList<>();
        for (LlmTool tool : candidates) {
            if (allowed.contains(tool.getName())) {
                filtered.add(ContextResource.builder()
                    .name(tool.getName())
                    .description(defaultString(tool.getDescription()))
                    .kind("tool")
                    .llmTool(tool)
                    .build());
            }
        }
        return filtered;
    }

    private List<String> resolveActToolNames(Agent agent) {
        List<String> names = new ArrayList<>();
        if (agent.getActToolList() != null) {
            names.addAll(agent.getActToolList());
        }
        if (agent.getMcpActServers() != null) {
            for (String server : agent.getMcpActServers()) {
                names.addAll(toolRegistry.getServerToolNames(server));
            }
        }
        return names.stream().filter(name -> name != null && !name.isBlank()).distinct().toList();
    }

    private List<String> resolveObserveToolNames(Agent agent) {
        List<String> names = new ArrayList<>();
        if (agent.getObserveToolList() != null) {
            names.addAll(agent.getObserveToolList());
        }
        if (agent.getMcpObserveServers() != null) {
            for (String server : agent.getMcpObserveServers()) {
                names.addAll(toolRegistry.getServerToolNames(server));
            }
        }
        return names.stream().filter(name -> name != null && !name.isBlank()).distinct().toList();
    }

    private List<Task> listChildren(String sessionId, String parentTaskId) {
        return taskService.listBySession(sessionId).stream()
            .filter(t -> t != null && parentTaskId.equals(t.getParentTaskId()))
            .toList();
    }

    private String defaultString(String value) {
        return value == null ? "" : value;
    }

    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    private boolean asBoolean(Object value, boolean defaultValue) {
        if (value == null) {
            return defaultValue;
        }
        if (value instanceof Boolean b) {
            return b;
        }
        return Boolean.parseBoolean(String.valueOf(value));
    }

    private List<Map<String, Object>> toMessageMaps(List<MemoryItem> messages) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (MemoryItem message : messages) {
            Map<String, Object> item = new java.util.LinkedHashMap<>();
            item.put("id", message.getId());
            item.put("session_id", message.getSessionId());
            item.put("agent_id", message.getAgentId());
            item.put("role", message.getRole());
            item.put("content", message.getContent());
            item.put("task_id", message.getTaskId());
            item.put("created_at", message.getCreatedAt());
            result.add(item);
        }
        return result;
    }
}
