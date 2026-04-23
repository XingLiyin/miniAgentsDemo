package com.codex.miniagents.config;

import lombok.Getter;
import lombok.Setter;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

import java.nio.file.Path;
import java.nio.file.Paths;

@Getter
@Setter
@Configuration
@ConfigurationProperties(prefix = "miniagents")
public class MiniAgentsProperties {
    // 应用基本信息
    private String appName = "miniAgents";

    private String appVersion = "0.1.0";

    // 数据目录
    private Path dataDir = Paths.get("data");

    private Path skillsDir = Paths.get("resources/skills");

    private Path agentsDir = Paths.get("resources/agents");

    // LLM 配置
    private String llmOpenaiApiKey = "";

    private String llmOpenaiBaseUrl = "https://api.openai.com";

    private String llmAnthropicApiKey = "";

    private String llmAnthropicBaseUrl = "https://api.anthropic.com";

    private String llmDefaultModel = "gpt-4.1-mini";

    private String defaultLlmProvider = "openai-main";

    private int defaultLlmTimeoutSec = 3600;

    // Agent 默认配置
    private String agentDefaultSystemPrompt = "You are a helpful agent.";

    private String agentDefaultLlmName = "openai-main";

    // Agent Loop 默认参数
    private int defaultTokenBudget = 200_000;

    private int defaultRootMaxTurns = 20;

    private int defaultSummaryThreshold = 20;

    private int defaultShortWindowSize = 20;

    private String defaultAgentTemplateName = "default";

    private String defaultPlannerTemplateName = "planner";

    private int maxConcurrentAgents = 5;

    private int maxConcurrentTasks = 10;

    private int maxSpawnDepth = 1;

    private int maxRetries = 1;

    // Tool 约束
    private String bashExecCwd = "";

    private int bashExecTimeoutMs = 30_000;

    private int bashExecOutputLimitBytes = 65_536;

    private int httpRequestTimeoutMs = 10_000;

    private int httpResponseLimitBytes = 524_288;

    // 日志
    private String logLevel = "INFO";

    // 外部存储
    private String storeBaseUrl = "";

    private int storeTimeoutSec = 10;
}
