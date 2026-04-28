package com.codex.miniagents.agenttemplate;

import com.codex.miniagents.agenttemplate.definition.AgentDefContent;
import com.codex.miniagents.agenttemplate.definition.AgentDefMetadata;
import com.codex.miniagents.agenttemplate.definition.ToolSpec;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

@Component
public class AgentLoader {
    private static final Logger log = LoggerFactory.getLogger(AgentLoader.class);

    private final Yaml yaml = new Yaml();

    public List<AgentDefMetadata> scan(Path agentsDir) {
        if (agentsDir == null || !Files.exists(agentsDir)) {
            log.warn("AgentLoader: agents_dir '{}' not found, no agents loaded", agentsDir);
            return List.of();
        }

        List<AgentDefMetadata> results = new ArrayList<>();
        try (Stream<Path> stream = Files.list(agentsDir)) {
            stream.sorted().forEach(agentDir -> {
                if (!Files.isDirectory(agentDir)) {
                    return;
                }
                if (!Files.exists(agentDir.resolve("SOUL.md"))) {
                    return;
                }

                try {
                    AgentDefMetadata metadata = loadMetadata(agentDir);
                    results.add(metadata);
                    log.debug("AgentLoader: loaded agent '{}' from '{}'", metadata.getName(), agentDir);
                } catch (Exception e) {
                    log.warn("AgentLoader: failed to load '{}': {}", agentDir.getFileName(), e.getMessage(), e);
                }
            });
        } catch (IOException e) {
            throw new RuntimeException("Failed to scan agents dir: " + agentsDir, e);
        }

        return results;
    }

    public AgentDefMetadata loadMetadata(Path agentDir) {
        ParsedAgentMd soulParsed = parseAgentMd(readAll(agentDir.resolve("SOUL.md")));
        ParsedAgentMd roleParsed = Files.exists(agentDir.resolve("ROLE.md"))
            ? parseAgentMd(readAll(agentDir.resolve("ROLE.md")))
            : new ParsedAgentMd(Map.of(), "");

        ToolSpec actToolSpec = parseToolSpec(soulParsed.frontmatter().get("tools"));
        ToolSpec observeToolSpec = parseToolSpec(roleParsed.frontmatter().get("tools"));
        List<String> mcpActServers = parseStringList(soulParsed.frontmatter().get("mcp_servers"));
        List<String> mcpObserveServers = parseStringList(roleParsed.frontmatter().get("mcp_servers"));
        List<String> subagents = parseStringList(soulParsed.frontmatter().get("subagents"));

        return AgentDefMetadata.builder()
            .name(requireString(soulParsed.frontmatter().get("name"), agentDir))
            .version(defaultString(asString(soulParsed.frontmatter().get("version")), "1.0.0"))
            .description(trimToEmpty(asString(soulParsed.frontmatter().get("description"))))
            .actToolSpec(actToolSpec)
            .observeToolSpec(observeToolSpec)
            .mcpActServers(mcpActServers)
            .mcpObserveServers(mcpObserveServers)
            .subagents(subagents)
            .agentDir(agentDir)
            .build();
    }

    public AgentDefContent loadContent(Path agentDir) {
        AgentDefMetadata metadata = loadMetadata(agentDir);

        String soulMd = readBody(agentDir.resolve("SOUL.md"));
        String roleMd = Files.exists(agentDir.resolve("ROLE.md")) ? readBody(agentDir.resolve("ROLE.md")) : "";
        String toolsMd = Files.exists(agentDir.resolve("TOOLS.md")) ? readBody(agentDir.resolve("TOOLS.md")) : "";
        String styleMd = Files.exists(agentDir.resolve("STYLE.md")) ? readBody(agentDir.resolve("STYLE.md")) : "";

        return AgentDefContent.builder()
            .metadata(metadata)
            .soulMd(soulMd)
            .roleMd(roleMd)
            .toolsMd(toolsMd)
            .styleMd(styleMd)
            .build();
    }

    private String readBody(Path path) {
        String content = readAll(path);
        return parseAgentMd(content).body();
    }

    private String readAll(Path path) {
        try {
            return Files.readString(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new RuntimeException("Failed to read file: " + path, e);
        }
    }

    private ParsedAgentMd parseAgentMd(String content) {
        if (content == null || content.isBlank()) {
            return new ParsedAgentMd(Map.of(), "");
        }

        if (!content.startsWith("---")) {
            return new ParsedAgentMd(Map.of(), content.strip());
        }

        int frontmatterEnd = findFrontmatterEnd(content);
        if (frontmatterEnd < 0) {
            return new ParsedAgentMd(Map.of(), content.strip());
        }

        String frontmatterStr = content.substring(3, frontmatterEnd).strip();
        String body = content.substring(frontmatterEnd).strip();

        if (body.startsWith("---")) {
            body = body.substring(3).strip();
        }

        Map<String, Object> frontmatter = parseYamlMap(frontmatterStr);
        return new ParsedAgentMd(frontmatter, body);
    }

    private int findFrontmatterEnd(String content) {
        int index = 3;
        while (index < content.length()) {
            int lineStart = index;

            if (content.charAt(lineStart) == '\r' || content.charAt(lineStart) == '\n') {
                index++;
                continue;
            }

            int lineEnd = lineStart;
            while (lineEnd < content.length()
                && content.charAt(lineEnd) != '\n'
                && content.charAt(lineEnd) != '\r') {
                lineEnd++;
            }

            String line = content.substring(lineStart, lineEnd).trim();
            if ("---".equals(line)) {
                return lineStart;
            }

            index = lineEnd + 1;
        }
        return -1;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> parseYamlMap(String text) {
        if (text == null || text.isBlank()) {
            return Map.of();
        }

        Object loaded;
        try {
            loaded = yaml.load(text);
        } catch (Exception e) {
            throw new IllegalArgumentException("Failed to parse YAML frontmatter", e);
        }

        if (loaded == null) {
            return Map.of();
        }

        if (!(loaded instanceof Map<?, ?> rawMap)) {
            throw new IllegalArgumentException("YAML frontmatter must be a map/object");
        }

        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : rawMap.entrySet()) {
            result.put(String.valueOf(entry.getKey()), entry.getValue());
        }
        return result;
    }

    private List<String> parseStringList(Object value) {
        if (value instanceof List<?> rawList && !rawList.isEmpty()) {
            List<String> result = new ArrayList<>();
            for (Object item : rawList) {
                if (item != null) {
                    result.add(String.valueOf(item));
                }
            }
            return result;
        }
        return List.of();
    }

    private ToolSpec parseToolSpec(Object value) {
        if (value == null) {
            return ToolSpec.builder().build();
        }
        if (value instanceof List<?> rawList) {
            return ToolSpec.builder()
                .required(parseStringList(rawList))
                .forbidden(List.of())
                .build();
        }
        if (value instanceof Map<?, ?> map) {
            List<String> required = parseStringList(map.get("required"));
            List<String> forbidden = parseStringList(map.get("forbidden"));
            return ToolSpec.builder()
                .required(required)
                .forbidden(forbidden)
                .build();
        }
        return ToolSpec.builder().build();
    }

    private String asString(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private String requireString(Object value, Path agentDir) {
        String result = asString(value).strip();
        if (result.isEmpty()) {
            throw new IllegalArgumentException("Missing required frontmatter field 'name' in " + agentDir);
        }
        return result;
    }

    private String trimToEmpty(String value) {
        return value == null ? "" : value.strip();
    }

    private String defaultString(String value, String defaultValue) {
        return (value == null || value.isBlank()) ? defaultValue : value;
    }

    private record ParsedAgentMd(Map<String, Object> frontmatter, String body) {
    }
}
