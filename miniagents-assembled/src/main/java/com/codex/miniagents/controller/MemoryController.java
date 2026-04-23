package com.codex.miniagents.controller;

import com.codex.miniagents.domain.memory.model.MemoryItem;
import com.codex.miniagents.domain.memory.model.MemorySummary;
import com.codex.miniagents.domain.memory.service.MemoryService;
import com.codex.miniagents.dto.request.AppendMessageRequest;
import com.codex.miniagents.dto.response.MessageResponse;
import com.codex.miniagents.dto.response.SummaryResponse;
import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.infrastructure.storage.repository.AgentRepository;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

@RestController
@RequestMapping("/api/v1/memories")
@RequiredArgsConstructor
@Slf4j
public class MemoryController {
    private final MemoryService memoryService;
    private final AgentRepository agentRepository;

    @GetMapping("/sessions/{sessionId}/messages")
    public List<MessageResponse> listSessionMessages(@PathVariable String sessionId) {
        log.info("Listing messages for session: sessionId='{}'", sessionId);
        List<String> agentIds = agentRepository.listBySession(sessionId);
        List<MemoryItem> all = new ArrayList<>();
        for (String agentId : agentIds) {
            all.addAll(memoryService.getAllMessages(agentId));
        }
        all.sort(Comparator.comparing(MemoryItem::getCreatedAt, Comparator.nullsLast(Comparator.naturalOrder())));
        return all.stream().map(MemoryItem::toResponse).toList();
    }

    @GetMapping("/agents/{agentId}/messages")
    public List<MessageResponse> listMessages(@PathVariable String agentId,
        @RequestParam(defaultValue = "50") int limit) {
        log.info("Listing messages for agent: agentId='{}', limit={}", agentId, limit);
        return memoryService.getWindow(agentId, limit).stream().map(MemoryItem::toResponse).toList();
    }

    @PostMapping("/agents/{agentId}/messages")
    @ResponseStatus(HttpStatus.CREATED)
    public MessageResponse appendMessage(@PathVariable String agentId,
        @Valid @RequestBody AppendMessageRequest request) {
        log.info("Appending message for agent: agentId='{}', role='{}'", agentId, request.getRole());
        MemoryItem item = memoryService.appendMessage(null, agentId, request.getRole(), request.getContent(),
            request.getTaskId());
        return item.toResponse();
    }

    @GetMapping("/agents/{agentId}/summary")
    public SummaryResponse getSummary(@PathVariable String agentId) {
        log.info("Fetching summary for agent: agentId='{}'", agentId);
        MemorySummary summary = memoryService.getSummary(agentId);
        if (summary == null) {
            throw new AppException(ErrorCode.SUMMARY_NOT_FOUND, "No summary yet");
        }
        return summary.toResponse();
    }
}
