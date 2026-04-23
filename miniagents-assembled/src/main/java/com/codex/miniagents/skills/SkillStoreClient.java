package com.codex.miniagents.skills;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.dto.response.SearchResponse;
import com.codex.miniagents.dto.response.SearchResult;

import lombok.RequiredArgsConstructor;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Component
@RequiredArgsConstructor
public class SkillStoreClient {
    private static final Logger logger = LoggerFactory.getLogger(SkillStoreClient.class);

    private final MiniAgentsProperties properties;

    private final RestTemplate restTemplate;

    public boolean isEnabled() {
        String baseUrl = properties.getStoreBaseUrl();
        return baseUrl != null && !baseUrl.isBlank();
    }

    /**
     * 将 skill 列表 upsert 到外部 DB。失败只记 warning，不中断调用方。
     */
    public void upsert(List<Map<String, Object>> skills) {
        if (!isEnabled() || skills == null || skills.isEmpty()) {
            return;
        }

        try {
            Map<String, Object> body = new HashMap<>();
            body.put("skills", skills);

            restTemplate.postForEntity(baseUrl() + "/skills/upsert", body, Void.class);
            logger.debug("SkillStoreClient: upserted {} skill(s)", skills.size());
        } catch (Exception e) {
            logger.warn("SkillStoreClient.upsert failed", e);
        }
    }

    /**
     * 从外部 DB 删除指定 skill。失败只记 warning。
     */
    public void delete(List<String> names) {
        if (!isEnabled() || names == null || names.isEmpty()) {
            return;
        }

        try {
            Map<String, Object> body = new HashMap<>();
            body.put("names", names);

            restTemplate.postForEntity(baseUrl() + "/skills/delete", body, Void.class);
            logger.debug("SkillStoreClient: deleted {} skill(s)", names.size());
        } catch (Exception e) {
            logger.warn("SkillStoreClient.delete failed for {}", names, e);
        }
    }

    /**
     * 语义搜索 skill；未启用或出错时返回空列表。
     */
    public List<SearchResult> search(String query, int topK) {
        if (!isEnabled()) {
            return List.of();
        }

        try {
            Map<String, Object> body = new HashMap<>();
            body.put("query", query);
            body.put("top_k", topK);

            ResponseEntity<SearchResponse> response = restTemplate.exchange(baseUrl() + "/skills/search",
                HttpMethod.POST, new HttpEntity<>(body), SearchResponse.class);

            SearchResponse responseBody = response.getBody();
            if (responseBody == null || responseBody.getResults() == null) {
                return List.of();
            }
            return responseBody.getResults();
        } catch (Exception e) {
            logger.warn("SkillStoreClient.search failed for query '{}'", query, e);
            return List.of();
        }
    }

    private String baseUrl() {
        return properties.getStoreBaseUrl().replaceAll("/+$", "");
    }
}
