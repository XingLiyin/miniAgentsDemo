package com.codex.miniagents.llm.model;

import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Getter
@Setter
@EqualsAndHashCode(callSuper = true)
@NoArgsConstructor
public class TextBlock extends LlmContentBlock {
    private String text;

    public TextBlock(String type, String text) {
        super(type);
        this.text = text;
    }
}
