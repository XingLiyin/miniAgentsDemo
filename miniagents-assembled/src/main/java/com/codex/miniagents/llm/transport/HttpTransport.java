package com.codex.miniagents.llm.transport;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.springframework.stereotype.Component;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Iterator;
import java.util.Map;
import java.util.NoSuchElementException;

@Component
public class HttpTransport implements Transport, StreamTransport {
    private final int timeout;

    private final ObjectMapper objectMapper = new ObjectMapper();

    private final HttpClient httpClient;

    public HttpTransport(MiniAgentsProperties properties) {
        this.timeout = properties.getDefaultLlmTimeoutSec();
        this.httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(timeout)).build();
    }

    @Override
    public Map<String, Object> post(String url, Map<String, String> headers, Map<String, Object> json, int timeout) {
        try {
            HttpRequest.Builder builder = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .timeout(Duration.ofSeconds(timeout > 0 ? timeout : this.timeout))
                .POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(json)));

            headers.forEach(builder::header);

            HttpResponse<String> response = httpClient.send(builder.build(),
                HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));

            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new RuntimeException("HTTP 状态错误: " + response.statusCode() + " " + response.body());
            }

            return objectMapper.readValue(response.body(), new TypeReference<>() {});
        } catch (java.net.http.HttpTimeoutException e) {
            throw new RuntimeException("HTTP 请求超时", e);
        } catch (Exception e) {
            throw new RuntimeException("HTTP 请求失败: " + e.getMessage(), e);
        }
    }

    @Override
    public Iterator<String> streamPost(String url, Map<String, String> headers, Map<String, Object> json, int timeout) {
        try {
            HttpRequest.Builder builder = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .timeout(Duration.ofSeconds(timeout > 0 ? timeout : this.timeout))
                .POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(json)));

            headers.forEach(builder::header);

            HttpResponse<InputStream> response = httpClient.send(builder.build(),
                HttpResponse.BodyHandlers.ofInputStream());

            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                String body = new String(response.body().readAllBytes(), StandardCharsets.UTF_8);
                throw new RuntimeException("HTTP 状态错误: " + response.statusCode() + " " + body);
            }

            BufferedReader reader = new BufferedReader(new InputStreamReader(response.body(), StandardCharsets.UTF_8));

            return new Iterator<>() {
                private String nextLine;

                private boolean finished = false;

                @Override
                public boolean hasNext() {
                    if (finished) {
                        return false;
                    }
                    if (nextLine != null) {
                        return true;
                    }

                    try {
                        String rawLine;
                        while ((rawLine = reader.readLine()) != null) {
                            String line = rawLine.trim();
                            if (!line.startsWith("data:")) {
                                continue;
                            }
                            String payload = line.substring("data:".length()).trim();
                            if ("[DONE]".equals(payload)) {
                                close();
                                finished = true;
                                return false;
                            }
                            nextLine = payload;
                            return true;
                        }
                        close();
                        finished = true;
                        return false;
                    } catch (Exception e) {
                        close();
                        throw new RuntimeException("HTTP 流式请求失败: " + e.getMessage(), e);
                    }
                }

                @Override
                public String next() {
                    if (!hasNext()) {
                        throw new NoSuchElementException();
                    }
                    String current = nextLine;
                    nextLine = null;
                    return current;
                }

                private void close() {
                    try {
                        reader.close();
                    } catch (Exception ignored) {
                    }
                }
            };
        } catch (java.net.http.HttpTimeoutException e) {
            throw new RuntimeException("HTTP 流式请求超时", e);
        } catch (Exception e) {
            throw new RuntimeException("HTTP 流式请求失败: " + e.getMessage(), e);
        }
    }
}
