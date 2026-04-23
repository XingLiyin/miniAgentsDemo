package com.codex.miniagents.tools.annotation;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Repeatable;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Repeatable(ToolSchemaRules.class)
@Documented
public @interface ToolSchemaRule {
    String ifField();

    String equals();

    String[] thenRequired() default {};
}
