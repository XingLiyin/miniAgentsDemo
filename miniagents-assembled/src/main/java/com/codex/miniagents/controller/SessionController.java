package com.codex.miniagents.controller;

import com.codex.miniagents.common.SseBus;
import com.codex.miniagents.dto.request.AnswerInputRequest;
import com.codex.miniagents.dto.request.CreateSessionRequest;
import com.codex.miniagents.dto.request.InitialTaskConfig;
import com.codex.miniagents.dto.request.SendMessageRequest;
import com.codex.miniagents.dto.response.SessionResponse;
import com.codex.miniagents.dto.response.TaskResponse;
import com.codex.miniagents.domain.model.session.Session;
import com.codex.miniagents.domain.model.session.SessionStatus;
import com.codex.miniagents.domain.memory.service.MemoryService;
import com.codex.miniagents.domain.service.SessionService;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.infrastructure.storage.file.EventStore;
import com.codex.miniagents.orchestrator.SessionManager;
import com.codex.miniagents.runtime.HitlStore;
import com.codex.miniagents.utils.AsyncUtils;

import jakarta.validation.Valid;
import lombok.extern.slf4j.Slf4j;

import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.LinkedHashMap;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;

@RestController
@RequestMapping("/api/v1/sessions")
@Slf4j
public class SessionController {
    private final SessionService sessionService;

    private final SessionManager sessionManager;

    private final TaskService taskService;
    private final MemoryService memoryService;

    public SessionController(SessionService sessionService, SessionManager sessionManager, TaskService taskService,
        MemoryService memoryService) {
        this.sessionService = sessionService;
        this.sessionManager = sessionManager;
        this.taskService = taskService;
        this.memoryService = memoryService;
    }

    @GetMapping
    public List<SessionResponse> listSessions() {
        log.info("Session list requested");
        return sessionService.listIds()
            .stream()
            .map(sessionService::get)
            .sorted(
                Comparator.comparing(Session::getCreatedAt, Comparator.nullsLast(Comparator.naturalOrder())).reversed())
            .map(SessionResponse::from)
            .toList();
    }

    @PostMapping
    @ResponseStatus(HttpStatus.ACCEPTED)
    public SessionResponse createSession(@Valid @RequestBody CreateSessionRequest request) {
        log.info("Creating session: templateId='{}', llmName='{}', llmModel='{}', hasInitialTask='{}'",
            request.getTemplateId(), request.getLlmName(), request.getLlmModel(), request.getInitialTask() != null);
        SessionManager.CreateSessionResult result = sessionManager.createSession(request.getUserPrompt(),
            request.getTemplateId(), request.getTokenBudget(), request.getRootMaxTurns(), request.getLlmName(),
            request.getLlmModel(), request.getInitialTask());
        sessionManager.scheduleLoop(result.session().getId(), result.agentId());
        log.info("Session created: sessionId='{}', agentId='{}'", result.session().getId(), result.agentId());
        return SessionResponse.from(result.session());
    }

    @GetMapping("/{sessionId}")
    public SessionResponse getSession(@PathVariable String sessionId) {
        log.info("Fetching session: sessionId='{}'", sessionId);
        return SessionResponse.from(sessionService.get(sessionId));
    }

    @PostMapping("/{sessionId}/cancel")
    public SessionResponse cancelSession(@PathVariable String sessionId) {
        log.info("Canceling session: sessionId='{}'", sessionId);
        return SessionResponse.from(sessionManager.cancelSession(sessionId));
    }

    @DeleteMapping("/{sessionId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void deleteSession(@PathVariable String sessionId) {
        log.info("Deleting session: sessionId='{}'", sessionId);
        sessionManager.deleteSession(sessionId);
    }

    @PostMapping("/{sessionId}/messages")
    public SessionResponse sendMessage(@PathVariable String sessionId, @Valid @RequestBody SendMessageRequest request) {
        log.info("Sending message to session: sessionId='{}', hasInitialTask='{}'", sessionId,
            request.getInitialTask() != null);
        return SessionResponse.from(sessionManager.continueSession(sessionId, request.getContent(), request.getInitialTask()));
    }

    @PostMapping("/{sessionId}/input")
    public SessionResponse answerInput(@PathVariable String sessionId, @Valid @RequestBody AnswerInputRequest request) {
        log.info("Answering human input: sessionId='{}'", sessionId);
        return SessionResponse.from(sessionManager.answerInput(sessionId, request.getContent()));
    }

    @GetMapping("/{sessionId}/tasks")
    public List<TaskResponse> listSessionTasks(@PathVariable String sessionId) {
        log.info("Listing tasks for session: sessionId='{}'", sessionId);
        return taskService.listBySession(sessionId)
            .stream()
            .filter(task -> !isDaemonTask(task))
            .map(TaskResponse::from)
            .toList();
    }

    @GetMapping(value = "/{sessionId}/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter streamSessionEvents(@PathVariable String sessionId) {
        log.info("Opening session event stream: sessionId='{}'", sessionId);
        Session session = sessionService.get(sessionId);
        SseEmitter emitter = new SseEmitter(0L);
        BlockingQueue<Map<String, Object>> queue = SseBus.getInstance().createSubscription(sessionId);

        AsyncUtils.runAsync(() -> {
            try {
                Map<String, Object> initEvent = new LinkedHashMap<>();
                initEvent.put("type", "init");
                initEvent.put("session", SessionResponse.from(session));
                initEvent.put("tasks", taskService.listBySession(sessionId).stream()
                    .filter(task -> !isDaemonTask(task))
                    .map(TaskResponse::from)
                    .toList());
                String agentId = session.getRootAgentId() == null ? "" : session.getRootAgentId();
                initEvent.put("messages", agentId.isBlank() ? List.of() : memoryService.getWindow(agentId, 500));
                emitter.send(SseEmitter.event().data(initEvent));

                List<Map<String, Object>> history = EventStore.getInstance().load(sessionId);
                if (!history.isEmpty()) {
                    Map<String, Object> historyEvent = new LinkedHashMap<>();
                    historyEvent.put("type", "history");
                    historyEvent.put("events", history);
                    emitter.send(SseEmitter.event().data(historyEvent));
                }

                if (session.getStatus() == SessionStatus.WAITING_INPUT) {
                    HitlStore.HitlEntry pending = HitlStore.getInstance().getPending(sessionId);
                    if (pending != null) {
                        Map<String, Object> waitingInputEvent = new LinkedHashMap<>();
                        waitingInputEvent.put("type", "waiting_input");
                        waitingInputEvent.put("prompt", pending.getPrompt());
                        waitingInputEvent.put("input_type", pending.getInputType());
                        waitingInputEvent.put("task_title", "等待用户输入");
                        emitter.send(SseEmitter.event().data(waitingInputEvent));
                    }
                }

                while (true) {
                    Map<String, Object> event = queue.poll(15, TimeUnit.SECONDS);
                    if (event != null) {
                        emitter.send(SseEmitter.event().data(event));
                        Object type = event.get("type");
                        if ("done".equals(String.valueOf(type))) {
                            break;
                        }
                    } else {
                        Map<String, Object> pingEvent = new LinkedHashMap<>();
                        pingEvent.put("type", "ping");
                        emitter.send(SseEmitter.event().data(pingEvent));
                    }
                }
                emitter.complete();
            } catch (Exception e) {
                emitter.completeWithError(e);
            } finally {
                SseBus.getInstance().removeSubscription(sessionId, queue);
            }
        });
        return emitter;
    }

    private boolean isDaemonTask(com.codex.miniagents.domain.model.task.Task task) {
        if (task == null || task.getSettings() == null) {
            return false;
        }
        Object daemon = task.getSettings().get("_daemon");
        if (daemon instanceof Boolean bool) {
            return bool;
        }
        return daemon != null && Boolean.parseBoolean(String.valueOf(daemon));
    }

}
