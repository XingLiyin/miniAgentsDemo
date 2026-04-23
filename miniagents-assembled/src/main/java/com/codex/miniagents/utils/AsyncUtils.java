package com.codex.miniagents.utils;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicInteger;

public final class AsyncUtils {
    private static final ThreadFactory THREAD_FACTORY = new ThreadFactory() {
        private final AtomicInteger counter = new AtomicInteger(1);

        @Override
        public Thread newThread(Runnable runnable) {
            Thread thread = new Thread(runnable, "miniagents-async-" + counter.getAndIncrement());
            thread.setDaemon(true);
            return thread;
        }
    };

    private static final ExecutorService DEFAULT_EXECUTOR = Executors.newCachedThreadPool(THREAD_FACTORY);

    private static volatile Executor executor = DEFAULT_EXECUTOR;

    private AsyncUtils() {
    }

    public static void setExecutor(Executor newExecutor) {
        executor = newExecutor == null ? DEFAULT_EXECUTOR : newExecutor;
    }

    public static Executor getExecutor() {
        return executor;
    }

    public static CompletableFuture<Void> runAsync(Runnable runnable) {
        return CompletableFuture.runAsync(runnable, executor);
    }

    public static <T> CompletableFuture<T> supplyAsync(java.util.function.Supplier<T> supplier) {
        return CompletableFuture.supplyAsync(supplier, executor);
    }
}
