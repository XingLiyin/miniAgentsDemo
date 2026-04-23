package com.codex.miniagents.llm.adapter;

import com.codex.miniagents.llm.model.LlmRequest;
import com.codex.miniagents.llm.model.LlmResponse;
import com.codex.miniagents.llm.model.ParsedResponse;
import com.codex.miniagents.llm.model.StreamChunk;

import java.util.Iterator;
import java.util.NoSuchElementException;

public abstract class LlmAdapter {
    public abstract LlmResponse complete(LlmRequest request);

    public Iterator<StreamChunk> stream(LlmRequest req) {
        LlmResponse response = complete(req);
        return new Iterator<>() {
            private int index = 0;

            @Override
            public boolean hasNext() {
                return index < 2;
            }

            @Override
            public StreamChunk next() {
                if (!hasNext()) {
                    throw new NoSuchElementException();
                }
                index++;
                if (index == 1) {
                    return StreamChunk.builder().textDelta(response.getText()).usage(response.getUsage()).build();
                }
                return StreamChunk.builder().done(true).usage(response.getUsage()).build();
            }
        };
    }

    public abstract ParsedResponse parseResponse(LlmResponse response);
}
