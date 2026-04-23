package com.codex.miniagents.controller;

import com.codex.miniagents.domain.model.tool.ToolCall;
import com.codex.miniagents.dto.response.ToolCallResponse;
import com.codex.miniagents.infrastructure.storage.repository.ToolCallRepository;

import lombok.extern.slf4j.Slf4j;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/tools")
@Slf4j
public class ToolsController {
    private final ToolCallRepository toolCallRepository;

    public ToolsController(ToolCallRepository toolCallRepository) {
        this.toolCallRepository = toolCallRepository;
    }

    @GetMapping("/sessions/{sessionId}/tool-calls")
    public List<ToolCallResponse> listToolCalls(@PathVariable String sessionId) {
        log.info("Listing tool calls for session: sessionId='{}'", sessionId);
        List<ToolCall> calls = toolCallRepository.findBySessionId(sessionId);
        java.util.Set<String> seenIds = new java.util.HashSet<>();
        java.util.List<ToolCall> finalCalls = new java.util.ArrayList<>();
        for (int i = calls.size() - 1; i >= 0; i--) {
            ToolCall c = calls.get(i);
            if (!seenIds.contains(c.getId())) {
                seenIds.add(c.getId());
                finalCalls.add(c);
            }
        }
        java.util.Collections.reverse(finalCalls);
        return finalCalls.stream().map(ToolCallResponse::from).toList();
    }
}
