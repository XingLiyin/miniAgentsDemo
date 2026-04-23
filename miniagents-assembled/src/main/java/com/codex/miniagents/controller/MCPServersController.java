package com.codex.miniagents.controller;

import com.codex.miniagents.dto.request.McpHttpRegisterRequest;
import com.codex.miniagents.dto.request.McpStdioRegisterRequest;
import com.codex.miniagents.dto.response.McpServerResponse;
import com.codex.miniagents.tools.mcp.McpService;

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
@RequestMapping("/api/v1/mcp-servers")
@Slf4j
public class MCPServersController {
    private final McpService mcpService;

    public MCPServersController(McpService mcpService) {
        this.mcpService = mcpService;
    }

    @GetMapping
    public List<McpServerResponse> listAll() {
        log.info("Listing MCP servers");
        return mcpService.listAll().stream().map(McpServerResponse::from).toList();
    }

    @GetMapping("/{name}")
    public McpServerResponse get(@PathVariable String name) {
        log.info("Fetching MCP server: name='{}'", name);
        return McpServerResponse.from(mcpService.get(name));
    }

    @PostMapping("/stdio")
    @ResponseStatus(org.springframework.http.HttpStatus.CREATED)
    public McpServerResponse registerStdio(@Valid @RequestBody McpStdioRegisterRequest request) {
        log.info("Registering stdio MCP server: name='{}'", request.getName());
        return McpServerResponse.from(
            mcpService.registerStdio(request.getName(), request.getCommand(), request.getArgs(), request.getEnv()));
    }

    @PostMapping("/http")
    @ResponseStatus(org.springframework.http.HttpStatus.CREATED)
    public McpServerResponse registerHttp(@Valid @RequestBody McpHttpRegisterRequest request) {
        log.info("Registering HTTP MCP server: name='{}'", request.getName());
        return McpServerResponse.from(
            mcpService.registerHttp(request.getName(), request.getUrl(), request.getTimeout()));
    }

    @PostMapping("/{name}/refresh")
    public McpServerResponse refresh(@PathVariable String name) {
        log.info("Refreshing MCP server: name='{}'", name);
        return McpServerResponse.from(mcpService.refresh(name));
    }

    @DeleteMapping("/{name}")
    @ResponseStatus(org.springframework.http.HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String name) {
        log.info("Deleting MCP server: name='{}'", name);
        mcpService.delete(name);
    }
}
