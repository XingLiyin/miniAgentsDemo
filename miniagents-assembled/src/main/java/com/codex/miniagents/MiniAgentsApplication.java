package com.codex.miniagents;

import lombok.extern.slf4j.Slf4j;

import org.springframework.boot.WebApplicationType;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@Slf4j
@SpringBootApplication
public class MiniAgentsApplication {

    public static void main(String[] args) {
        System.out.println("MiniAgentsApplication: starting");
        log.info("MiniAgentsApplication: starting");
        SpringApplication app = new SpringApplication(MiniAgentsApplication.class);
        app.setWebApplicationType(WebApplicationType.SERVLET);
        app.run(args);
        System.out.println("MiniAgentsApplication: started");
        log.info("MiniAgentsApplication: started");
    }
}
