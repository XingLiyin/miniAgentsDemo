package com.codex.miniagents.llm.adapter;

import com.codex.miniagents.llm.model.LlmContentBlock;
import com.codex.miniagents.llm.model.LlmRequest;
import com.codex.miniagents.llm.model.LlmResponse;
import com.codex.miniagents.llm.model.LlmUsage;
import com.codex.miniagents.llm.model.ParsedResponse;
import com.codex.miniagents.llm.model.StreamChunk;
import com.codex.miniagents.llm.model.TextBlock;

import java.util.Iterator;
import java.util.List;
import java.util.HashMap;
import java.util.ArrayList;

public class MockAdapter extends LlmAdapter {
    private final String responseText;

    public MockAdapter() {
        this("mock");
    }

    public MockAdapter(String responseText) {
        this.responseText = responseText;
    }

    @Override
    public LlmResponse complete(LlmRequest request) {
        return LlmResponse.builder()
            .text(responseText)
            .raw(new HashMap<>())
            .usage(LlmUsage.builder().promptTokens(10).completionTokens(5).totalTokens(15).build())
            .build();
    }

    @Override
    public ParsedResponse parseResponse(LlmResponse response) {
        List<LlmContentBlock> blocks = new ArrayList<>();
        blocks.add(new TextBlock("text", response.getText()));
        return ParsedResponse.builder()
            .text(response.getText())
            .blocks(blocks)
            .toolCalls(new ArrayList<>())
            .raw(response.getRaw())
            .usage(response.getUsage())
            .build();
    }

    @Override
    public Iterator<StreamChunk> stream(LlmRequest req) {
        String[] words = responseText.isEmpty() ? new String[0] : responseText.split("\\s+");
        return new Iterator<>() {
            private int index = 0;

            @Override
            public boolean hasNext() {
                return index <= words.length;
            }

            @Override
            public StreamChunk next() {
                if (!hasNext()) {
                    throw new java.util.NoSuchElementException();
                }
                if (index == words.length) {
                    index++;
                    return StreamChunk.builder()
                        .done(true)
                        .usage(LlmUsage.builder()
                            .promptTokens(10)
                            .completionTokens(words.length)
                            .totalTokens(10 + words.length)
                            .build())
                        .build();
                }
                String delta = index == 0 ? words[index] : " " + words[index];
                index++;
                return StreamChunk.builder().textDelta(delta).build();
            }
        };
    }
}
