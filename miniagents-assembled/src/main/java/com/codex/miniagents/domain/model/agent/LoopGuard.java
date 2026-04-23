package com.codex.miniagents.domain.model.agent;

import lombok.Getter;

@Getter
public class LoopGuard {

    private int turnsUsed;
    private int maxTurns;
    private int actorMaxToolRounds;
    private int observerMaxToolRounds;

    public LoopGuard() {
        this(0, 20, 50, 50);
    }

    public LoopGuard(int turnsUsed, int maxTurns, int actorMaxToolRounds) {
        this(turnsUsed, maxTurns, actorMaxToolRounds, 50);
    }

    public LoopGuard(int turnsUsed, int maxTurns, int actorMaxToolRounds, int observerMaxToolRounds) {
        this.turnsUsed = turnsUsed;
        this.maxTurns = maxTurns;
        this.actorMaxToolRounds = actorMaxToolRounds;
        this.observerMaxToolRounds = observerMaxToolRounds;
    }

    public void setTurnsUsed(int turnsUsed) {
        this.turnsUsed = turnsUsed;
    }

    public void setMaxTurns(int maxTurns) {
        this.maxTurns = maxTurns;
    }

    public void setActorMaxToolRounds(int actorMaxToolRounds) {
        this.actorMaxToolRounds = actorMaxToolRounds;
    }

    public void setObserverMaxToolRounds(int observerMaxToolRounds) {
        this.observerMaxToolRounds = observerMaxToolRounds;
    }

    public void incrementTurnsUsed() {
        this.turnsUsed += 1;
    }

    public boolean maxTurnsExceeded() {
        return this.turnsUsed >= this.maxTurns;
    }
}
