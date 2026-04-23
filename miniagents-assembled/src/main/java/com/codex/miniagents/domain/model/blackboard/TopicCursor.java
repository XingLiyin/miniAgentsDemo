package com.codex.miniagents.domain.model.blackboard;

import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
public class TopicCursor {
    private String agentId;

    private String topic;

    private int lastRead = 0;
}
