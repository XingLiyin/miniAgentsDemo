package com.codex.miniagents.tools.builtin;

import lombok.RequiredArgsConstructor;

import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.time.Duration;
import java.util.Map;

@Component
@RequiredArgsConstructor
public class BuiltinHttpToolExecutor {
    private final RestTemplateBuilder restTemplateBuilder;

    public HttpToolResponse execute(String url, String method, Map<String, Object> headers, String body,
        int timeoutMs) {
        try {
            RestTemplate restTemplate = restTemplateBuilder.setConnectTimeout(Duration.ofMillis(timeoutMs))
                .setReadTimeout(Duration.ofMillis(timeoutMs))
                .build();

            HttpHeaders httpHeaders = new HttpHeaders();
            if (headers != null) {
                headers.forEach((k, v) -> {
                    if (v != null) {
                        httpHeaders.set(k, String.valueOf(v));
                    }
                });
            }

            HttpEntity<String> entity = new HttpEntity<>(body, httpHeaders);
            ResponseEntity<String> response = restTemplate.exchange(url,
                HttpMethod.valueOf(method.toUpperCase(java.util.Locale.ROOT)), entity, String.class);

            return new HttpToolResponse(response.getStatusCode().value(),
                response.getBody() == null ? "" : response.getBody());
        } catch (ResourceAccessException e) {
            throw new HttpToolTimeoutException(e);
        } catch (RestClientException e) {
            throw e;
        }
    }

    public record HttpToolResponse(int statusCode, String body) {}

    public static class HttpToolTimeoutException extends RuntimeException {
        public HttpToolTimeoutException(Throwable cause) {
            super(cause);
        }
    }
}
