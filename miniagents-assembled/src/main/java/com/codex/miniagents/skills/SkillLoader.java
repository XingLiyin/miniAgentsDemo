package com.codex.miniagents.skills;

import com.codex.miniagents.skills.model.SkillMetadata;

import lombok.AllArgsConstructor;
import lombok.Data;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

@Component
public class SkillLoader {
    private static final Logger logger = LoggerFactory.getLogger(SkillLoader.class);

    /**
     * 扫描 skills 目录并解析 SKILL.md 文件。
     */
    public List<SkillMetadata> scan(Path skillsDir) {
        if (skillsDir == null || !Files.exists(skillsDir)) {
            logger.warn("SkillLoader: skills_dir '{}' not found, no skills loaded", skillsDir);
            return Collections.emptyList();
        }

        List<SkillMetadata> result = new ArrayList<>();
        try (Stream<Path> stream = Files.list(skillsDir)) {
            List<Path> children = stream.sorted(Comparator.comparing(path -> path.getFileName().toString())).toList();

            for (Path skillDir : children) {
                if (!Files.isDirectory(skillDir)) {
                    continue;
                }

                Path skillMd = skillDir.resolve("SKILL.md");
                if (!Files.exists(skillMd)) {
                    continue;
                }

                try {
                    SkillMetadata metadata = loadMetadata(skillMd, skillDir);
                    result.add(metadata);
                    logger.debug("SkillLoader: loaded skill '{}' from '{}'", metadata.getName(), skillDir);
                } catch (Exception e) {
                    logger.warn("SkillLoader: failed to load '{}': {}", skillDir.getFileName(), e.getMessage());
                }
            }
        } catch (Exception e) {
            logger.warn("SkillLoader: failed to scan '{}'", skillsDir, e);
        }

        return result;
    }

    /**
     * 解析 SKILL.md frontMatter，返回 SkillMetadata（Level 1）。
     */
    public SkillMetadata loadMetadata(Path skillMdPath, Path skillDir) throws IOException {
        String content = Files.readString(skillMdPath, StandardCharsets.UTF_8);
        ParsedSkillMd parsed = parseSkillMd(content);
        Map<String, Object> frontMatter = parsed.getFrontMatter();

        Object nameValue = frontMatter.get("name");
        if (nameValue == null) {
            throw new IllegalArgumentException("Missing required frontMatter field: name");
        }

        Object descriptionValue = frontMatter.getOrDefault("description", "");
        Object versionValue = frontMatter.getOrDefault("version", "1.0");
        Object triggersValue = frontMatter.get("triggers");

        List<String> triggers = new ArrayList<>();
        if (triggersValue instanceof List<?> rawList) {
            for (Object item : rawList) {
                triggers.add(String.valueOf(item));
            }
        }

        return SkillMetadata.builder()
            .name(String.valueOf(nameValue))
            .description(String.valueOf(descriptionValue).trim())
            .triggers(triggers)
            .version(String.valueOf(versionValue))
            .skillDir(skillDir)
            .source("local")
            .build();
    }

    /**
     * 读取 SKILL.md 主体（frontMatter 之后的部分）—— Level 2。
     */
    public String loadInstructions(Path skillDir) throws IOException {
        Path skillMd = skillDir.resolve("SKILL.md");
        String content = Files.readString(skillMd, StandardCharsets.UTF_8);
        ParsedSkillMd parsed = parseSkillMd(content);
        return parsed.getBody();
    }

    /**
     * 读取 skill 目录内的资源文件 —— Level 3。
     * <p>
     * 安全检查：resourcePath 不能逃出 skillDir。
     */
    public String loadResource(Path skillDir, String resourcePath) throws IOException {
        Path fullPath = skillDir.resolve(resourcePath).normalize().toAbsolutePath();
        Path rootPath = skillDir.toAbsolutePath().normalize();

        if (!fullPath.startsWith(rootPath)) {
            throw new IllegalArgumentException("Resource path '%s' escapes skill directory".formatted(resourcePath));
        }

        return Files.readString(fullPath, StandardCharsets.UTF_8);
    }

    /**
     * 将 SKILL.md 内容分离为 (frontMatter_dict, body_str)。
     * <p>
     * frontMatter 以 '---' 开头和结尾包裹，body 是其余部分。
     */
    private ParsedSkillMd parseSkillMd(String content) {
        if (!content.startsWith("---")) {
            return new ParsedSkillMd(Collections.emptyMap(), content.strip());
        }

        int end = content.indexOf("\n---", 3);
        if (end == -1) {
            return new ParsedSkillMd(Collections.emptyMap(), content.strip());
        }

        String frontMatterStr = content.substring(3, end).trim();
        String body = content.substring(end + 4).trim();
        Map<String, Object> frontMatter = parseSimpleYaml(frontMatterStr);
        return new ParsedSkillMd(frontMatter, body);
    }

    /**
     * 轻量级 YAML 解析，支持 SKILL.md frontMatter 所需格式：
     * <p>
     * - 简单键值：  key: value
     * - 折叠字符串：key: >
     * line1
     * line2
     * - 列表：      key:
     * - item1
     * - item2
     * - 带引号值：  key: "value" 或 key: 'value'
     */
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

            if (">".equals(rawVal) || "|".equals(rawVal)) {
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
                List<String> items = new ArrayList<>();
                i++;
                while (i < lines.size() && lines.get(i).trim().startsWith("- ")) {
                    String item = lines.get(i).trim().substring(2).trim();
                    items.add(item);
                    i++;
                }
                result.put(key, items);
                continue;
            }

            result.put(key, stripQuotes(rawVal));
            i++;
        }

        return result;
    }

    private String stripQuotes(String value) {
        if (value == null || value.length() < 2) {
            return value;
        }
        if ((value.startsWith("\"") && value.endsWith("\"")) || (value.startsWith("'") && value.endsWith("'"))) {
            return value.substring(1, value.length() - 1);
        }
        return value;
    }

    @Data
    @AllArgsConstructor
    private static class ParsedSkillMd {
        private Map<String, Object> frontMatter;

        private String body;
    }
}
