package com.codex.miniagents.config;

import lombok.RequiredArgsConstructor;

import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestTemplate;

import java.time.Duration;

@Configuration
@RequiredArgsConstructor
public class HttpClientConfig {
    private final MiniAgentsProperties properties;

    @Bean("restTemplate")
    public RestTemplate restTemplate(RestTemplateBuilder restTemplateBuilder) {
        int timeout = Math.max(1, properties.getStoreTimeoutSec());
        return restTemplateBuilder.setConnectTimeout(Duration.ofSeconds(timeout))
            .setReadTimeout(Duration.ofSeconds(timeout))
            .build();
    }
}
