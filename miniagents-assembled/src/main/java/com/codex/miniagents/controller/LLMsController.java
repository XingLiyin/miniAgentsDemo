package com.codex.miniagents.controller;

import com.codex.miniagents.dto.request.LLMDefaultModelRequest;
import com.codex.miniagents.dto.request.LLMModelRequest;
import com.codex.miniagents.dto.request.LLMRegisterRequest;
import com.codex.miniagents.dto.response.LLMRegisterResponse;
import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.exception.ApiErrorResponse;
import com.codex.miniagents.llm.model.LlmProviderConfig;
import com.codex.miniagents.llm.registry.LlmRegistry;

import jakarta.validation.Valid;
import lombok.extern.slf4j.Slf4j;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/llms")
@Slf4j
public class LLMsController {
    private final LlmRegistry llmRegistry;
    private final MiniAgentsProperties properties;

    public LLMsController(LlmRegistry llmRegistry, MiniAgentsProperties properties) {
        this.llmRegistry = llmRegistry;
        this.properties = properties;
    }

    @PostMapping
    public ResponseEntity<?> registerLlm(@Valid @RequestBody LLMRegisterRequest req) {
        log.info("Registering LLM provider: name='{}', style='{}'", req.getName(), req.getStyle());
        if (llmRegistry.isRegistered(req.getName())) {
            return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(new ApiErrorResponse("LLM_ALREADY_EXISTS", "LLM '" + req.getName() + "' already registered"));
        }

        if (!LlmRegistry.SUPPORTED_LLM_STYLES.contains(req.getStyle())) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(new ApiErrorResponse("INVALID_LLM_STYLE", "Unsupported LLM style: " + req.getStyle()));
        }

        int timeoutSec = req.getTimeoutSec() == null ? properties.getDefaultLlmTimeoutSec() : req.getTimeoutSec();
        int maxTokens = req.getMaxTokens() == null
            ? LlmProviderConfig.DEFAULT_MAX_TOKENS
            : req.getMaxTokens();
        List<String> models = req.getModels() == null ? List.of() : req.getModels().stream()
            .filter(m -> m != null && !m.isBlank())
            .distinct()
            .toList();
        String defaultModel = req.getDefaultModel();
        if ((defaultModel == null || defaultModel.isBlank()) && !models.isEmpty()) {
            defaultModel = models.get(0);
        }

        LlmProviderConfig config = new LlmProviderConfig(req.getName(), req.getStyle(), req.getApiKey(),
            req.getBaseUrl(), models, defaultModel == null ? "" : defaultModel, timeoutSec, maxTokens);

        try {
            llmRegistry.register(config, true);
            log.info("LLM provider registered: name='{}'", req.getName());
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(new ApiErrorResponse("LLM_ALREADY_EXISTS", e.getMessage()));
        }

        return ResponseEntity.status(HttpStatus.CREATED).body(LLMRegisterResponse.from(config));
    }

    @GetMapping
    public List<LLMRegisterResponse> listLlms() {
        log.info("Listing LLM providers");
        return llmRegistry.listConfigs().stream().map(LLMRegisterResponse::from).toList();
    }

    @GetMapping("/{name}")
    public ResponseEntity<?> getLlm(@PathVariable String name) {
        log.info("Fetching LLM provider: name='{}'", name);
        if (!llmRegistry.isRegistered(name)) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(new ApiErrorResponse("LLM_NOT_FOUND", "LLM '" + name + "' not found"));
        }

        LlmProviderConfig c = llmRegistry.getConfig(name);
        return ResponseEntity.ok(LLMRegisterResponse.from(c));
    }

    @DeleteMapping("/{name}")
    public ResponseEntity<?> deleteLlm(@PathVariable String name) {
        log.info("Deleting LLM provider: name='{}'", name);
        if (!llmRegistry.isRegistered(name)) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(new ApiErrorResponse("LLM_NOT_FOUND", "LLM '" + name + "' not found"));
        }

        llmRegistry.delete(name);
        log.info("LLM provider deleted: name='{}'", name);
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/{name}/models")
    public ResponseEntity<?> addModel(@PathVariable String name, @Valid @RequestBody LLMModelRequest req) {
        log.info("Adding model to provider: name='{}', model='{}'", name, req.getModel());
        if (!llmRegistry.isRegistered(name)) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(new ApiErrorResponse("LLM_NOT_FOUND", "LLM '" + name + "' not found"));
        }
        return ResponseEntity.ok(LLMRegisterResponse.from(llmRegistry.addModel(name, req.getModel())));
    }

    @DeleteMapping("/{name}/models")
    public ResponseEntity<?> removeModel(@PathVariable String name, @Valid @RequestBody LLMModelRequest req) {
        log.info("Removing model from provider: name='{}', model='{}'", name, req.getModel());
        if (!llmRegistry.isRegistered(name)) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(new ApiErrorResponse("LLM_NOT_FOUND", "LLM '" + name + "' not found"));
        }
        return ResponseEntity.ok(LLMRegisterResponse.from(llmRegistry.removeModel(name, req.getModel())));
    }

    @PutMapping("/{name}/default_model")
    public ResponseEntity<?> setDefaultModel(@PathVariable String name, @Valid @RequestBody LLMDefaultModelRequest req) {
        log.info("Setting default model for provider: name='{}', model='{}'", name, req.getModel());
        if (!llmRegistry.isRegistered(name)) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(new ApiErrorResponse("LLM_NOT_FOUND", "LLM '" + name + "' not found"));
        }
        try {
            return ResponseEntity.ok(LLMRegisterResponse.from(llmRegistry.setDefaultModel(name, req.getModel())));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(new ApiErrorResponse("INVALID_MODEL", e.getMessage()));
        }
    }
}
