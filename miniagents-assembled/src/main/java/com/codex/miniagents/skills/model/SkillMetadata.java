package com.codex.miniagents.skills.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class SkillMetadata {
    private String name;

    private String description;

    @Builder.Default
    private List<String> triggers = new ArrayList<>();

    @Builder.Default
    private String version = "1.0.0";

    private Path skillDir;

    @Builder.Default
    private String source = "local";

    private String remoteSourceName;

    @Builder.Default
    private Map<String, Object> extra = new LinkedHashMap<>();
}
