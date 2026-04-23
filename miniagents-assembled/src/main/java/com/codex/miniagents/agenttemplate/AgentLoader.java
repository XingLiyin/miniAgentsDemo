package com.codex.miniagents.agenttemplate;

import com.codex.miniagents.agenttemplate.definition.AgentDefContent;
import com.codex.miniagents.agenttemplate.definition.AgentDefMetadata;
import com.codex.miniagents.agenttemplate.definition.ToolSpec;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

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
                    log.warn("AgentLoader: failed to load '{}': {}", agentDir.getFileName(), e.getMessage());
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

        return AgentDefMetadata.builder()
            .name(requireString(soulParsed.frontmatter().get("name"), agentDir))
            .version(defaultString(asString(soulParsed.frontmatter().get("version")), "1.0.0"))
            .description(trimToEmpty(asString(soulParsed.frontmatter().get("description"))))
            .actToolSpec(actToolSpec)
            .observeToolSpec(observeToolSpec)
            .mcpActServers(mcpActServers)
            .mcpObserveServers(mcpObserveServers)
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
        if (content == null) {
            return new ParsedAgentMd(Map.of(), "");
        }

        if (!content.startsWith("---")) {
            return new ParsedAgentMd(Map.of(), content.strip());
        }

        int end = content.indexOf("\n---", 3);
        if (end == -1) {
            return new ParsedAgentMd(Map.of(), content.strip());
        }

        String frontmatterStr = content.substring(3, end).strip();
        String body = content.substring(end + 4).strip();
        Map<String, Object> frontmatter = parseSimpleYaml(frontmatterStr);
        return new ParsedAgentMd(frontmatter, body);
    }

    private Map<String, Object> parseSimpleYaml(String text) {
        Map<String, Object> result = new LinkedHashMap<>();
        List<String> lines = text.lines().toList();
        int i = 0;

        while (i < lines.size()) {
            String line = lines.get(i);

            if (line.isEmpty() || line.startsWith("#") || line.startsWith(" ")) {
                i++;
                continue;
            }

            int colonIndex = line.indexOf(':');
            if (colonIndex < 0) {
                i++;
                continue;
            }

            String key = line.substring(0, colonIndex).trim();
            String rawVal = line.substring(colonIndex + 1).trim();

            if (rawVal.equals("[]")) {
                result.put(key, List.of());
                i++;
                continue;
            }

            if (rawVal.equals(">") || rawVal.equals("|")) {
                List<String> parts = new ArrayList<>();
                i++;
                while (i < lines.size() && lines.get(i).startsWith("  ")) {
                    parts.add(lines.get(i).trim());
                    i++;
                }
                result.put(key, String.join(" ", parts));
                continue;
            }

            if (rawVal.isEmpty()) {
                ParsedIndentedValue nested = parseIndentedValue(lines, i + 1);
                result.put(key, nested.value());
                i = nested.nextIndex();
                continue;
            }

            result.put(key, stripQuotes(rawVal));
            i++;
        }

        return result;
    }

    private ParsedIndentedValue parseIndentedValue(List<String> lines, int startIndex) {
        if (startIndex >= lines.size()) {
            return new ParsedIndentedValue(List.of(), startIndex);
        }

        List<String> listValues = new ArrayList<>();
        Map<String, Object> mapValues = new LinkedHashMap<>();
        boolean sawMap = false;
        int i = startIndex;

        while (i < lines.size()) {
            String line = lines.get(i);
            if (!line.startsWith("  ")) {
                break;
            }
            String trimmed = line.trim();
            if (trimmed.startsWith("- ")) {
                listValues.add(trimmed.substring(2).trim());
                i++;
                continue;
            }

            int colonIndex = trimmed.indexOf(':');
            if (colonIndex < 0) {
                i++;
                continue;
            }

            sawMap = true;
            String key = trimmed.substring(0, colonIndex).trim();
            String rawVal = trimmed.substring(colonIndex + 1).trim();
            if (rawVal.isEmpty()) {
                ParsedIndentedValue nested = parseIndentedValue(lines, i + 1);
                mapValues.put(key, nested.value());
                i = nested.nextIndex();
                continue;
            }
            if (rawVal.equals("[]")) {
                mapValues.put(key, List.of());
                i++;
                continue;
            }
            mapValues.put(key, stripQuotes(rawVal));
            i++;
        }

        return sawMap ? new ParsedIndentedValue(mapValues, i) : new ParsedIndentedValue(listValues, i);
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

    private String stripQuotes(String value) {
        if (value == null || value.length() < 2) {
            return value;
        }
        if ((value.startsWith("\"") && value.endsWith("\""))
            || (value.startsWith("'") && value.endsWith("'"))) {
            return value.substring(1, value.length() - 1);
        }
        return value;
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

    private record ParsedIndentedValue(Object value, int nextIndex) {
    }
}
