package com.codex.miniagents.tools;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.dto.request.ToolStoreUpsertRequest;
import com.codex.miniagents.dto.response.SearchResponse;
import com.codex.miniagents.dto.response.SearchResult;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.util.List;
import java.util.Map;

@Slf4j
@Component
@RequiredArgsConstructor
public class ToolStoreClient {
    private final MiniAgentsProperties properties;

    private final RestTemplate restTemplate;

    public boolean isEnabled() {
        String baseUrl = properties.getStoreBaseUrl();
        return baseUrl != null && !baseUrl.isBlank();
    }

    public void upsert(ToolStoreUpsertRequest request) {
        if (!isEnabled()) {
            return;
        }
        String provider = request.getTools().get(0).getProvider();
        try {
            restTemplate.postForEntity(baseUrl() + "/tools/upsert", request, Void.class);
            log.debug("ToolStoreClient: upserted {} tool(s) for provider '{}'", request.getTools().size(), provider);
        } catch (RestClientException e) {
            log.warn("ToolStoreClient.upsert failed for provider '{}'", provider, e);
        }
    }

    public void delete(List<String> names) {
        if (!isEnabled() || names == null || names.isEmpty()) {
            return;
        }

        try {
            Map<String, Object> body = Map.of("names", names);
            restTemplate.postForEntity(baseUrl() + "/tools/delete", body, Void.class);
            log.debug("ToolStoreClient: deleted {} tool(s)", names.size());
        } catch (RestClientException e) {
            log.warn("ToolStoreClient.delete failed for {}", names, e);
        }
    }

    @SuppressWarnings("unchecked")
    public List<SearchResult> search(String query, int topK) {
        if (!isEnabled()) {
            return List.of();
        }

        try {
            Map<String, Object> body = Map.of("query", query, "top_k", topK);

            ResponseEntity<SearchResponse> response = restTemplate.postForEntity(baseUrl() + "/tools/search",
                body, SearchResponse.class);

            SearchResponse responseBody = response.getBody();
            if (responseBody == null || responseBody.getResults() == null) {
                return List.of();
            }
            return responseBody.getResults();
        } catch (RestClientException e) {
            log.warn("ToolStoreClient.search failed for query '{}'", query, e);
            return List.of();
        }
    }

    private String baseUrl() {
        return properties.getStoreBaseUrl().replaceAll("/+$", "");
    }
}
