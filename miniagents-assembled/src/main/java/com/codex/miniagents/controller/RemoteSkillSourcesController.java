package com.codex.miniagents.controller;

import com.codex.miniagents.domain.service.RemoteSkillSourceService;
import com.codex.miniagents.dto.request.RemoteSkillSourceHttpRegisterRequest;
import com.codex.miniagents.dto.request.RemoteSkillSourceStdioRegisterRequest;
import com.codex.miniagents.dto.response.RemoteSkillSourceResponse;
import com.codex.miniagents.skills.model.RemoteSkillSourceConfig;

import jakarta.validation.Valid;
import lombok.extern.slf4j.Slf4j;

import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/remote-skill-sources")
@Slf4j
public class RemoteSkillSourcesController {
    private final RemoteSkillSourceService remoteSkillSourceService;

    public RemoteSkillSourcesController(RemoteSkillSourceService remoteSkillSourceService) {
        this.remoteSkillSourceService = remoteSkillSourceService;
    }

    @PostMapping("/http")
    @ResponseStatus(org.springframework.http.HttpStatus.CREATED)
    public RemoteSkillSourceResponse registerHttp(@Valid @RequestBody RemoteSkillSourceHttpRegisterRequest request) {
        log.info("Registering HTTP remote skill source: sourceName='{}'", request.getSourceName());
        RemoteSkillSourceConfig config = baseConfig(request.getSourceName(),
            request.getMcpToolListSkills(),
            request.getMcpToolLoadSkillMd(),
            request.getMcpToolGetSkillFiles(),
            request.getMcpToolLoadSkillReference(),
            request.getMcpToolExecSkillScript());
        config.setMcpType("http");
        config.setMcpUrl(request.getMcpUrl());
        config.setMcpTimeout(request.getMcpTimeout() == null ? 30 : request.getMcpTimeout());
        return RemoteSkillSourceResponse.from(remoteSkillSourceService.register(config));
    }

    @PostMapping("/stdio")
    @ResponseStatus(org.springframework.http.HttpStatus.CREATED)
    public RemoteSkillSourceResponse registerStdio(@Valid @RequestBody RemoteSkillSourceStdioRegisterRequest request) {
        log.info("Registering stdio remote skill source: sourceName='{}'", request.getSourceName());
        RemoteSkillSourceConfig config = baseConfig(request.getSourceName(),
            request.getMcpToolListSkills(),
            request.getMcpToolLoadSkillMd(),
            request.getMcpToolGetSkillFiles(),
            request.getMcpToolLoadSkillReference(),
            request.getMcpToolExecSkillScript());
        config.setMcpType("stdio");
        config.setMcpCommand(request.getMcpCommand());
        config.setMcpArgs(request.getMcpArgs() == null ? List.of() : List.copyOf(request.getMcpArgs()));
        config.setMcpEnv(request.getMcpEnv() == null ? java.util.Map.of() : java.util.Map.copyOf(request.getMcpEnv()));
        return RemoteSkillSourceResponse.from(remoteSkillSourceService.register(config));
    }

    @GetMapping
    public List<RemoteSkillSourceResponse> listAll() {
        log.info("Listing remote skill sources");
        return remoteSkillSourceService.listAll().stream().map(RemoteSkillSourceResponse::from).toList();
    }

    @GetMapping("/{sourceName}")
    public RemoteSkillSourceResponse get(@PathVariable String sourceName) {
        log.info("Fetching remote skill source: sourceName='{}'", sourceName);
        return RemoteSkillSourceResponse.from(remoteSkillSourceService.get(sourceName));
    }

    @DeleteMapping("/{sourceName}")
    @ResponseStatus(org.springframework.http.HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String sourceName) {
        log.info("Deleting remote skill source: sourceName='{}'", sourceName);
        remoteSkillSourceService.delete(sourceName);
    }

    private RemoteSkillSourceConfig baseConfig(String sourceName, String listSkillsTool, String loadSkillMdTool,
        String getSkillFilesTool, String loadSkillReferenceTool, String execSkillScriptTool) {
        return RemoteSkillSourceConfig.builder()
            .sourceName(sourceName)
            .mcpToolListSkills(defaultString(listSkillsTool, "listSkills"))
            .mcpToolLoadSkillMd(defaultString(loadSkillMdTool, "loadSkillMd"))
            .mcpToolGetSkillFiles(defaultString(getSkillFilesTool, "getSkillFiles"))
            .mcpToolLoadSkillReference(defaultString(loadSkillReferenceTool, "loadSkillReference"))
            .mcpToolExecSkillScript(defaultString(execSkillScriptTool, "execSkillScript"))
            .build();
    }

    private String defaultString(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }
}
