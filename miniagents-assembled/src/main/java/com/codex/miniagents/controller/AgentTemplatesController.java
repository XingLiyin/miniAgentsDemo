package com.codex.miniagents.controller;

import com.codex.miniagents.dto.response.AgentTemplateResponse;
import com.codex.miniagents.domain.service.AgentTemplateService;

import lombok.extern.slf4j.Slf4j;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/agent-templates")
@Slf4j
public class AgentTemplatesController {
    private final AgentTemplateService agentTemplateService;

    public AgentTemplatesController(AgentTemplateService agentTemplateService) {
        this.agentTemplateService = agentTemplateService;
    }

    @GetMapping
    public List<AgentTemplateResponse> listTemplates() {
        log.info("Listing agent templates");
        return agentTemplateService.listAll().stream().map(AgentTemplateResponse::from).toList();
    }

    @GetMapping("/{id}")
    public AgentTemplateResponse getTemplate(@PathVariable String id) {
        log.info("Fetching agent template: id='{}'", id);
        return AgentTemplateResponse.from(agentTemplateService.get(id));
    }
}
