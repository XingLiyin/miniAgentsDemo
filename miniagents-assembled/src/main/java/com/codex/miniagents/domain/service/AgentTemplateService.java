package com.codex.miniagents.domain.service;

import com.codex.miniagents.domain.model.AgentTemplate;
import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.infrastructure.storage.repository.AgentTemplateRepository;
import com.codex.miniagents.llm.ChatClient;
import com.codex.miniagents.llm.model.LlmMessage;
import com.codex.miniagents.llm.model.LlmRequest;
import com.codex.miniagents.llm.model.LlmResponse;
import com.codex.miniagents.utils.IdUtils;
import com.codex.miniagents.utils.TimeUtils;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Optional;

@Service
public class AgentTemplateService {
    private static final Logger log = LoggerFactory.getLogger(AgentTemplateService.class);

    private final AgentTemplateRepository repository;

    private final ObjectMapper objectMapper = new ObjectMapper();

    public AgentTemplateService(AgentTemplateRepository repository) {
        this.repository = repository;
    }

    public AgentTemplate create(
        String name,
        List<String> actToolList,
        List<String> observeToolList,
        String description,
        String sourceDir
    ) {
        String now = TimeUtils.nowIso();
        AgentTemplate template = AgentTemplate.builder()
            .id(IdUtils.newTemplateId())
            .name(name)
            .actToolList(actToolList == null ? new ArrayList<>() : new ArrayList<>(actToolList))
            .observeToolList(observeToolList == null ? new ArrayList<>() : new ArrayList<>(observeToolList))
            .description(defaultString(description))
            .sourceDir(defaultString(sourceDir))
            .createdAt(now)
            .updatedAt(now)
            .build();

        repository.save(template);
        return template;
    }

    public AgentTemplate get(String id) {
        return repository.findById(id)
            .orElseThrow(() -> new AppException(
                ErrorCode.TEMPLATE_NOT_FOUND,
                "AgentTemplate " + id + " not found"
            ));
    }

    public List<AgentTemplate> listAll() {
        return repository.findAll();
    }

    public void delete(String templateId) {
        AgentTemplate existing = repository.findById(templateId)
            .orElseThrow(() -> new AppException(
                ErrorCode.TEMPLATE_NOT_FOUND,
                "AgentTemplate " + templateId + " not found"
            ));
        repository.delete(existing.getId());
    }

    public AgentTemplate upsertByName(
        String name,
        String version,
        String description,
        List<String> actToolList,
        List<String> observeToolList,
        List<String> mcpActServers,
        List<String> mcpObserveServers,
        String sourceDir
    ) {
        Optional<AgentTemplate> existingOpt = repository.findByName(name);
        String now = TimeUtils.nowIso();

        if (existingOpt.isPresent()) {
            AgentTemplate existing = existingOpt.get();
            existing.setVersion(defaultString(version));
            existing.setDescription(defaultString(description));
            existing.setActToolList(actToolList == null ? new ArrayList<>() : new ArrayList<>(actToolList));
            existing.setObserveToolList(observeToolList == null ? new ArrayList<>() : new ArrayList<>(observeToolList));
            existing.setMcpActServers(mcpActServers == null ? new ArrayList<>() : new ArrayList<>(mcpActServers));
            existing.setMcpObserveServers(mcpObserveServers == null ? new ArrayList<>() : new ArrayList<>(mcpObserveServers));
            existing.setSourceDir(defaultString(sourceDir));
            existing.setUpdatedAt(now);

            repository.save(existing);
            log.debug("AgentTemplateService: upserted (updated) template '{}'", name);
            return existing;
        }

        AgentTemplate template = AgentTemplate.builder()
            .id(IdUtils.newTemplateId())
            .name(name)
            .version(defaultString(version))
            .description(defaultString(description))
            .actToolList(actToolList == null ? new ArrayList<>() : new ArrayList<>(actToolList))
            .observeToolList(observeToolList == null ? new ArrayList<>() : new ArrayList<>(observeToolList))
            .mcpActServers(mcpActServers == null ? new ArrayList<>() : new ArrayList<>(mcpActServers))
            .mcpObserveServers(mcpObserveServers == null ? new ArrayList<>() : new ArrayList<>(mcpObserveServers))
            .sourceDir(defaultString(sourceDir))
            .createdAt(now)
            .updatedAt(now)
            .build();

        repository.save(template);
        log.debug("AgentTemplateService: upserted (created) template '{}'", name);
        return template;
    }

    public AgentTemplate getOrPrepare(String templateId, ChatClient chatClient) {
        return get(templateId);
    }

    public AgentTemplate getByName(String name) {
        return repository.findByName(name).orElse(null);
    }

    private List<String> extractToolList(String toolsMd, ChatClient chatClient) {
        String prompt = "Read the tool usage guide below and extract all tool names.\n"
            + "Return a JSON array of strings only. Example: [\"http_request\", \"bash_exec\"]\n\n"
            + "Tool guide:\n" + toolsMd;

        try {
            LlmRequest request = LlmRequest.builder()
                .messages(List.of(LlmMessage.builder().role("user").content(prompt).build()))
                .systemPrompt("You extract tool names from documentation. Output only a JSON array.")
                .build();

            LlmResponse response = chatClient.sendMessage(request);
            return objectMapper.readValue(response.getText().trim(), new TypeReference<>() {});
        } catch (Exception e) {
            log.warn("LlmToolListExtractor: extraction failed", e);
            return Collections.emptyList();
        }
    }

    private String defaultString(String value) {
        return value == null ? "" : value;
    }

    private String defaultString(String value, String defaultValue) {
        return (value == null || value.isBlank()) ? defaultValue : value;
    }

    private boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }
}
