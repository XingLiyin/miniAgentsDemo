package com.codex.miniagents.llm.transport;

import java.util.Iterator;
import java.util.Map;

public interface StreamTransport {
    Iterator<String> streamPost(String url, Map<String, String> headers, Map<String, Object> json, int timeout);
}
