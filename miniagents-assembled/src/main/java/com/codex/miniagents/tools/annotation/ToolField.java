package com.codex.miniagents.tools.annotation;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Target(ElementType.FIELD)
@Retention(RetentionPolicy.RUNTIME)
@Documented
public @interface ToolField {
    String value() default "";

    boolean required() default false;

    boolean nullable() default false;

    String[] allowableValues() default {};

    String condition() default "";
}
