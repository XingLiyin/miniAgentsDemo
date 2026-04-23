package com.codex.miniagents.tools.registry;

import com.codex.miniagents.tools.ToolDefinitionFactory;
import com.codex.miniagents.tools.annotation.ToolSpec;
import com.codex.miniagents.tools.builtin.BuiltinToolsService;
import com.codex.miniagents.tools.provider.BuiltinToolProvider;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

@Configuration
public class BuiltinToolsConfiguration {
    @Bean
    public BuiltinToolProvider builtinToolProvider(BuiltinToolsService builtinToolsService) {
        List<com.codex.miniagents.tools.model.ToolDefinition> definitions = new ArrayList<>();

        for (Method method : BuiltinToolsService.class.getDeclaredMethods()) {
            if (method.isAnnotationPresent(ToolSpec.class)) {
                definitions.add(ToolDefinitionFactory.fromMethod(builtinToolsService, method));
            }
        }

        return new BuiltinToolProvider(
            definitions.stream().filter(def -> !"request_human_input".equals(def.getName())).toList());
    }
}
