package com.codex.miniagents.runtime;

import com.codex.miniagents.domain.model.agent.Agent;
import com.codex.miniagents.domain.model.session.Session;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.model.task.TaskStatus;
import com.codex.miniagents.domain.service.BlackboardService;
import com.codex.miniagents.domain.memory.model.MemorySummary;
import com.codex.miniagents.domain.memory.service.MemoryService;
import com.codex.miniagents.domain.service.SessionService;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.infrastructure.storage.repository.AgentRepository;
import com.codex.miniagents.runtime.compaction.CompactionResult;
import com.codex.miniagents.runtime.compaction.CompactionStrategy;
import com.codex.miniagents.runtime.model.ActorResult;
import com.codex.miniagents.runtime.model.ObserverVerdict;
import com.codex.miniagents.runtime.model.ReasoningContext;
import com.codex.miniagents.runtime.model.TaskReview;
import com.codex.miniagents.runtime.model.ReviewStatus;
import com.codex.miniagents.runtime.model.ToolCallRecord;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Slf4j
@Component
public class AgentLoop {
    private final SessionService sessionService;

    private final TaskService taskService;

    private final MemoryService memoryService;

    private final BlackboardService blackboardService;

    private final AgentRepository agentRepository;

    private final Reasoner reasoner;

    private final Actor actor;

    private final Observer observer;

    private final CompactionStrategy compactionStrategy;

    public AgentLoop(SessionService sessionService, TaskService taskService, MemoryService memoryService,
        BlackboardService blackboardService, AgentRepository agentRepository, Reasoner reasoner,
        Actor actor, Observer observer, ObjectProvider<CompactionStrategy> compactionStrategyProvider) {
        this.sessionService = sessionService;
        this.taskService = taskService;
        this.memoryService = memoryService;
        this.blackboardService = blackboardService;
        this.agentRepository = agentRepository;
        this.reasoner = reasoner;
        this.actor = actor;
        this.observer = observer;
        this.compactionStrategy = compactionStrategyProvider.getIfAvailable();
    }

    public void run(String sessionId, String agentId, String taskId) {
        Session session = sessionService.get(sessionId);
        Agent agent = agentRepository.findById(agentId)
            .orElseThrow(() -> new IllegalArgumentException("Agent not found: " + agentId));
        agent.markRunning();
        agentRepository.save(agent);
        Task task = taskService.get(taskId);

        try {
            if (session.getTokenUsed() >= session.getTokenBudget()) {
                throw new AppException(
                    ErrorCode.TOKEN_BUDGET_EXCEEDED,
                    "Session " + sessionId + " token budget exhausted (" + session.getTokenUsed() + "/"
                        + session.getTokenBudget() + ")"
                );
            }

            if (agent.getLoopGuard() != null) {
                agent.getLoopGuard().incrementTurnsUsed();
                agentRepository.save(agent);
                if (agent.getLoopGuard().getTurnsUsed() > agent.getLoopGuard().getMaxTurns()) {
                    throw new AppException(
                        ErrorCode.MAX_TURNS_EXCEEDED,
                        "Agent " + agentId + " reached max_turns=" + agent.getLoopGuard().getMaxTurns()
                    );
                }
            }

            ReasoningContext context = reasoner.reason(session, agent, task);
            ActorResult result = actor.act(task, context, agent);
            task = taskService.get(taskId);
            if (task.getStatus() == TaskStatus.SUSPENDED) {
                return;
            }
            if (task.getStatus() != TaskStatus.TO_BE_OBSERVED
                && task.getStatus() != TaskStatus.FINISHED
                && task.getStatus() != TaskStatus.FAILED
                && task.getStatus() != TaskStatus.CANCELED) {
                taskService.toBeObserved(taskId);
                task = taskService.get(taskId);
            }
            List<Task> taskList = taskService.listBySession(sessionId);
            ObserverVerdict verdict = observer.observe(session, agent, result, context, task, taskList);

            task = taskService.get(taskId);

            if (task.getUserPrompt() != null && !task.getUserPrompt().isBlank()) {
                memoryService.appendMessage(sessionId, agentId, "user", task.getUserPrompt(), taskId);
            }
            if (result.getConversationTurns() != null) {
                result.getConversationTurns().forEach(turn -> {
                    if (turn.getToolCalls() != null && !turn.getToolCalls().isEmpty()) {
                        memoryService.appendMessage(
                            sessionId,
                            agentId,
                            "assistant",
                            defaultString(turn.getLlmText()),
                            taskId,
                            null,
                            toToolCallMaps(turn.getToolCalls())
                        );
                        for (ToolCallRecord call : turn.getToolCalls()) {
                            memoryService.appendMessage(
                                sessionId,
                                agentId,
                                "tool",
                                defaultString(call.getResult()),
                                taskId,
                                call.getToolCallId(),
                                List.of()
                            );
                        }
                    } else if (turn.getLlmText() != null && !turn.getLlmText().isBlank()) {
                        memoryService.appendMessage(sessionId, agentId, "assistant", turn.getLlmText(), taskId);
                    }
                });
            }
            if (verdict.getSummary() != null && !verdict.getSummary().isBlank()) {
                memoryService.appendMessage(sessionId, agentId, "assistant", verdict.getSummary(), taskId);
            }

            if ("FAILED".equals(task.getStatus() == null ? null : task.getStatus().name())) {
                String error = task.getError() == null || task.getError().isBlank()
                    ? (task.getResult() == null ? "Task failed by observer" : task.getResult())
                    : task.getError();
                throw new AppException(ErrorCode.TASK_FAILED_BY_OBSERVER, error);
            }

            if (task.getStatus() == TaskStatus.PENDING) {
                return;
            }

            if (task.getStatus() == TaskStatus.FINISHED && task.getResult() != null && !task.getResult().isBlank()) {
                blackboardService.publish(sessionId, task.getId(), agentId, task.getResult());
            }

            if (verdict.getTaskReviews() != null) {
                for (TaskReview review : verdict.getTaskReviews()) {
                    if (review == null || task.getId().equals(review.getTaskId())) {
                        continue;
                    }
                    try {
                        if (review.getReviewStatus() == ReviewStatus.REOPEN) {
                            taskService.reopen(review.getTaskId());
                            log.info("Task {} reopened by observer: {}", review.getTaskId(), review.getReasoning());
                        } else if (review.getReviewStatus() == ReviewStatus.SKIP) {
                            taskService.finish(review.getTaskId(),
                                review.getReasoning() == null || review.getReasoning().isBlank()
                                    ? "Completed indirectly per observer."
                                    : review.getReasoning());
                            log.info("Task {} skipped by observer: {}", review.getTaskId(), review.getReasoning());
                        }
                    } catch (Exception e) {
                        log.warn("Failed to apply task review for {}: {}", review.getTaskId(), e.getMessage());
                    }
                }
            }

            if (result.getConversationTurns() != null) {
                result.getConversationTurns().forEach(turn -> {
                    blackboardService.publish(sessionId, "_root", "agent_id_" + agentId,
                        turn.getLlmText() == null ? "" : turn.getLlmText());
                    if (turn.getToolCalls() != null) {
                        turn.getToolCalls().forEach(call -> blackboardService.publish(
                            sessionId,
                            "_root",
                            "agent_id_" + agentId,
                            "Tool call: " + call.getToolName() + "(" + call.getArguments() + ") -> "
                                + call.getResult() + " (error=" + call.isError() + ")"
                        ));
                    }
                });
            }

            if (memoryService.shouldSummarize(agentId, null)) {
                doSummarize(sessionId, agentId, verdict.getSummary());
            }
        } catch (AppException e) {
            log.error("AgentLoop error: session={} code={} msg={}", sessionId, e.getCode(), e.getMessage());
            throw e;
        } finally {
            Agent latest = agentRepository.findById(agentId).orElse(agent);
            if (latest.getStatus() == com.codex.miniagents.domain.model.agent.AgentStatus.RUNNING) {
                latest.markFinished();
                agentRepository.save(latest);
            }
        }
    }

    private void doSummarize(String sessionId, String agentId, String latestSummary) {
        String summaryText = latestSummary == null ? "" : latestSummary;
        List<com.codex.miniagents.domain.memory.model.MemoryItem> messages = memoryService.getWindow(agentId, 10_000);
        if (compactionStrategy != null) {
            CompactionResult compacted = compactionStrategy.compact(messages);
            if (compacted != null && compacted.summaryText() != null && !compacted.summaryText().isBlank()) {
                summaryText = compacted.summaryText();
            }
        }
        MemorySummary summary = new MemorySummary();
        summary.setSessionId(sessionId);
        summary.setAgentId(agentId);
        summary.setSummaryText(summaryText);
        summary.setCoveredUpTo(messages.size());
        summary.setCreatedAt(java.time.Instant.now().toString());
        memoryService.saveSummary(agentId, summary);
    }

    private String defaultString(String value) {
        return value == null ? "" : value;
    }

    private List<Map<String, Object>> toToolCallMaps(List<ToolCallRecord> toolCalls) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (ToolCallRecord call : toolCalls) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", call.getToolCallId());
            item.put("name", call.getToolName());
            item.put("input", call.getArguments() == null ? Map.of() : call.getArguments());
            result.add(item);
        }
        return result;
    }
}
