package com.codex.miniagents.llm.transport;

import java.util.Map;

public interface Transport {
    Map<String, Object> post(String url, Map<String, String> headers, Map<String, Object> json, int timeout);
}
