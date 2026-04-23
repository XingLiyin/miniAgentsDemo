package com.codex.miniagents.tools;

import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.llm.model.InputSchema;
import com.codex.miniagents.tools.annotation.ToolField;
import com.codex.miniagents.tools.annotation.ToolParam;
import com.codex.miniagents.tools.annotation.ToolSchemaRule;
import com.codex.miniagents.tools.annotation.ToolSchemaRules;
import com.codex.miniagents.tools.annotation.ToolSpec;
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.model.ToolDefinition;
import com.codex.miniagents.tools.model.ToolResult;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonIgnore;

import lombok.experimental.UtilityClass;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.GenericArrayType;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Parameter;
import java.lang.reflect.ParameterizedType;
import java.lang.reflect.Type;
import java.time.temporal.Temporal;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Date;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@UtilityClass
public class ToolDefinitionFactory {
    private static final Set<Class<?>> SIMPLE_SCALAR_TYPES = Set.of(String.class, Integer.class, int.class, Long.class,
        long.class, Short.class, short.class, Byte.class, byte.class, Double.class, double.class, Float.class,
        float.class, Boolean.class, boolean.class, Character.class, char.class);

    public static ToolDefinition fromMethod(Object target, Method method) {
        ToolSpec spec = method.getAnnotation(ToolSpec.class);
        if (spec == null) {
            throw new IllegalArgumentException("Method must be annotated with @ToolSpec: " + method);
        }

        InputSchema inputSchema = buildMethodInputSchema(method);

        return ToolDefinition.builder()
            .name(spec.name())
            .description(spec.description())
            .inputSchema(inputSchema)
            .metadata(Map.of())
            .handler((arguments, context) -> invoke(target, method, arguments, context))
            .build();
    }

    private static InputSchema buildMethodInputSchema(Method method) {
        Map<String, Object> properties = new LinkedHashMap<>();
        List<String> required = new ArrayList<>();

        Parameter[] parameters = method.getParameters();
        for (Parameter parameter : parameters) {
            ToolParam toolParam = parameter.getAnnotation(ToolParam.class);
            if (toolParam == null) {
                continue;
            }

            String paramName = resolveParameterName(parameter);
            Map<String, Object> propertySchema = resolveTypeSchema(parameter.getParameterizedType(), new HashSet<>());

            propertySchema.put("description", toolParam.value());
            properties.put(paramName, propertySchema);

            if (toolParam.required()) {
                required.add(paramName);
            }
        }

        return InputSchema.builder().type("object").properties(properties).required(required).build();
    }

    private static Map<String, Object> resolveTypeSchema(Type type, Set<Type> visiting) {
        if (type instanceof Class<?> clazz) {
            return resolveClassSchema(clazz, visiting);
        }

        if (type instanceof ParameterizedType parameterizedType) {
            Type rawType = parameterizedType.getRawType();

            if (rawType instanceof Class<?> rawClass) {
                if (Collection.class.isAssignableFrom(rawClass)) {
                    Map<String, Object> schema = new LinkedHashMap<>();
                    schema.put("type", "array");

                    Type itemType = parameterizedType.getActualTypeArguments().length > 0
                        ? parameterizedType.getActualTypeArguments()[0]
                        : Object.class;
                    schema.put("items", resolveTypeSchema(itemType, visiting));
                    return schema;
                }

                if (Map.class.isAssignableFrom(rawClass)) {
                    Map<String, Object> schema = new LinkedHashMap<>();
                    schema.put("type", "object");
                    schema.put("additionalProperties", true);
                    return schema;
                }

                if (Optional.class.isAssignableFrom(rawClass)
                    && parameterizedType.getActualTypeArguments().length == 1) {
                    Map<String, Object> inner = resolveTypeSchema(parameterizedType.getActualTypeArguments()[0],
                        visiting);
                    inner.put("nullable", true);
                    return inner;
                }

                return resolvePojoSchema(rawClass, visiting);
            }
        }

        if (type instanceof GenericArrayType genericArrayType) {
            Map<String, Object> schema = new LinkedHashMap<>();
            schema.put("type", "array");
            schema.put("items", resolveTypeSchema(genericArrayType.getGenericComponentType(), visiting));
            return schema;
        }

        Map<String, Object> fallback = new LinkedHashMap<>();
        fallback.put("type", "object");
        return fallback;
    }

    private static Map<String, Object> resolveClassSchema(Class<?> clazz, Set<Type> visiting) {
        if (clazz.isEnum()) {
            return resolveEnumSchema(clazz);
        }

        if (SIMPLE_SCALAR_TYPES.contains(clazz)) {
            return scalarSchema(clazz);
        }

        if (clazz == Object.class) {
            Map<String, Object> schema = new LinkedHashMap<>();
            schema.put("type", "object");
            return schema;
        }

        if (clazz.isArray()) {
            Map<String, Object> schema = new LinkedHashMap<>();
            schema.put("type", "array");
            schema.put("items", resolveTypeSchema(clazz.getComponentType(), visiting));
            return schema;
        }

        if (Collection.class.isAssignableFrom(clazz)) {
            Map<String, Object> schema = new LinkedHashMap<>();
            schema.put("type", "array");
            schema.put("items", Map.of("type", "object"));
            return schema;
        }

        if (Map.class.isAssignableFrom(clazz)) {
            Map<String, Object> schema = new LinkedHashMap<>();
            schema.put("type", "object");
            schema.put("additionalProperties", true);
            return schema;
        }

        if (UUID.class.isAssignableFrom(clazz) || Temporal.class.isAssignableFrom(clazz) || Date.class.isAssignableFrom(
            clazz)) {
            Map<String, Object> schema = new LinkedHashMap<>();
            schema.put("type", "string");
            return schema;
        }

        return resolvePojoSchema(clazz, visiting);
    }

    private static Map<String, Object> resolveEnumSchema(Class<?> enumClass) {
        Map<String, Object> schema = new LinkedHashMap<>();
        schema.put("type", "string");

        Object[] constants = enumClass.getEnumConstants();
        List<String> enumValues = new ArrayList<>();
        for (Object constant : constants) {
            enumValues.add(String.valueOf(constant));
        }
        schema.put("enum", enumValues);
        return schema;
    }

    private static Map<String, Object> scalarSchema(Class<?> clazz) {
        Map<String, Object> schema = new LinkedHashMap<>();

        if (clazz == String.class || clazz == Character.class || clazz == char.class) {
            schema.put("type", "string");
        } else if (clazz == Boolean.class || clazz == boolean.class) {
            schema.put("type", "boolean");
        } else if (clazz == Double.class || clazz == double.class || clazz == Float.class || clazz == float.class) {
            schema.put("type", "number");
        } else {
            schema.put("type", "integer");
        }

        return schema;
    }

    private static Map<String, Object> resolvePojoSchema(Class<?> clazz, Set<Type> visiting) {
        if (visiting.contains(clazz)) {
            Map<String, Object> recursive = new LinkedHashMap<>();
            recursive.put("type", "object");
            return recursive;
        }

        visiting.add(clazz);
        try {
            Map<String, Object> schema = new LinkedHashMap<>();
            schema.put("type", "object");

            Map<String, Object> properties = new LinkedHashMap<>();
            List<String> required = new ArrayList<>();

            for (Field field : getAllInstanceFields(clazz)) {
                if (field.isAnnotationPresent(JsonIgnore.class)) {
                    continue;
                }
                ToolField toolField = field.getAnnotation(ToolField.class);

                Map<String, Object> fieldSchema = resolveTypeSchema(field.getGenericType(), visiting);
                String schemaFieldName = resolveFieldName(field);

                if (toolField != null) {
                    if (!toolField.value().isBlank()) {
                        fieldSchema.put("description", toolField.value());
                    }
                    if (toolField.nullable()) {
                        fieldSchema.put("nullable", true);
                    }
                    if (toolField.allowableValues().length > 0) {
                        fieldSchema.put("enum", Arrays.asList(toolField.allowableValues()));
                    }
                    if (!toolField.condition().isBlank()) {
                        fieldSchema.put("x-condition", toolField.condition());
                    }
                    if (toolField.required()) {
                        required.add(schemaFieldName);
                    }
                }

                properties.put(schemaFieldName, fieldSchema);
            }

            schema.put("properties", properties);
            if (!required.isEmpty()) {
                schema.put("required", required);
            }

            appendConditionalRules(clazz, schema);
            return schema;
        } finally {
            visiting.remove(clazz);
        }
    }

    private static String resolveFieldName(Field field) {
        JsonProperty jsonProperty = field.getAnnotation(JsonProperty.class);
        if (jsonProperty != null && jsonProperty.value() != null && !jsonProperty.value().isBlank()) {
            return jsonProperty.value();
        }
        return field.getName();
    }

    private static List<Field> getAllInstanceFields(Class<?> clazz) {
        List<Field> fields = new ArrayList<>();
        Class<?> current = clazz;

        while (current != null && current != Object.class) {
            for (Field field : current.getDeclaredFields()) {
                if (Modifier.isStatic(field.getModifiers()) || field.isSynthetic()) {
                    continue;
                }
                fields.add(field);
            }
            current = current.getSuperclass();
        }

        return fields;
    }

    private static void appendConditionalRules(Class<?> clazz, Map<String, Object> schema) {
        List<Map<String, Object>> allOf = new ArrayList<>();

        ToolSchemaRule singleRule = clazz.getAnnotation(ToolSchemaRule.class);
        if (singleRule != null) {
            allOf.add(toConditionalRule(singleRule));
        }

        ToolSchemaRules multipleRules = clazz.getAnnotation(ToolSchemaRules.class);
        if (multipleRules != null) {
            for (ToolSchemaRule rule : multipleRules.value()) {
                allOf.add(toConditionalRule(rule));
            }
        }

        if (!allOf.isEmpty()) {
            schema.put("allOf", allOf);
        }
    }

    private static Map<String, Object> toConditionalRule(ToolSchemaRule rule) {
        Map<String, Object> ifPart = new LinkedHashMap<>();
        ifPart.put("properties", Map.of(rule.ifField(), Map.of("const", rule.equals())));

        Map<String, Object> thenPart = new LinkedHashMap<>();
        thenPart.put("required", Arrays.asList(rule.thenRequired()));

        Map<String, Object> condition = new LinkedHashMap<>();
        condition.put("if", ifPart);
        condition.put("then", thenPart);
        return condition;
    }

    private static ToolResult invoke(Object target, Method method, Map<String, Object> arguments,
        CallContext context) {
        try {
            Object[] invokeArgs = resolveArguments(method, arguments, context);
            Object result = method.invoke(target, invokeArgs);

            if (result instanceof ToolResult toolResult) {
                return toolResult;
            }

            return ToolResult.builder().content(result == null ? "" : String.valueOf(result)).build();
        } catch (InvocationTargetException e) {
            Throwable cause = e.getTargetException();

            if (cause instanceof AppException appException) {
                return ToolResult.builder()
                    .content(appException.getMessage())
                    .isError(true)
                    .errorCode(appException.getCode().name())
                    .build();
            }

            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, "Failed to invoke tool method: " + method.getName());
        } catch (AppException appException) {
            return ToolResult.builder()
                .content(appException.getMessage())
                .isError(true)
                .errorCode(appException.getCode().name())
                .build();
        } catch (Exception e) {
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, "Failed to invoke tool method: " + method.getName());
        }
    }

    private static Object[] resolveArguments(Method method, Map<String, Object> arguments, CallContext context) {
        Parameter[] parameters = method.getParameters();
        Object[] results = new Object[parameters.length];

        for (int i = 0; i < parameters.length; i++) {
            Parameter parameter = parameters[i];
            if (parameter.getAnnotation(ToolParam.class) == null
                && CallContext.class.isAssignableFrom(parameter.getType())) {
                results[i] = context == null ? CallContext.empty() : context;
                continue;
            }
            String name = resolveParameterName(parameter);
            Object raw = arguments == null ? null : arguments.get(name);
            results[i] = convert(raw, parameter.getParameterizedType());
        }

        return results;
    }

    @SuppressWarnings("unchecked")
    private static Object convert(Object raw, Type targetType) {
        if (raw == null) {
            if (targetType instanceof Class<?> clazz && clazz.isPrimitive()) {
                if (clazz == boolean.class) {
                    return false;
                }
                if (clazz == int.class) {
                    return 0;
                }
                if (clazz == long.class) {
                    return 0L;
                }
                if (clazz == double.class) {
                    return 0D;
                }
                if (clazz == float.class) {
                    return 0F;
                }
                if (clazz == short.class) {
                    return (short) 0;
                }
                if (clazz == byte.class) {
                    return (byte) 0;
                }
                if (clazz == char.class) {
                    return '\0';
                }
            }
            return null;
        }

        if (targetType instanceof Class<?> clazz) {
            if (clazz.isInstance(raw)) {
                return raw;
            }

            if (clazz == String.class) {
                return String.valueOf(raw);
            }
            if (clazz == Integer.class || clazz == int.class) {
                return Integer.parseInt(String.valueOf(raw));
            }
            if (clazz == Long.class || clazz == long.class) {
                return Long.parseLong(String.valueOf(raw));
            }
            if (clazz == Double.class || clazz == double.class) {
                return Double.parseDouble(String.valueOf(raw));
            }
            if (clazz == Float.class || clazz == float.class) {
                return Float.parseFloat(String.valueOf(raw));
            }
            if (clazz == Boolean.class || clazz == boolean.class) {
                return Boolean.parseBoolean(String.valueOf(raw));
            }

            if (clazz.isEnum()) {
                return Enum.valueOf((Class<? extends Enum>) clazz.asSubclass(Enum.class), String.valueOf(raw));
            }

            if (Map.class.isAssignableFrom(clazz)) {
                return raw;
            }

            if (List.class.isAssignableFrom(clazz)) {
                return raw;
            }

            if (raw instanceof Map<?, ?> rawMap) {
                return mapToPojo((Map<String, Object>) rawMap, clazz);
            }

            return raw;
        }

        if (targetType instanceof ParameterizedType parameterizedType) {
            Type rawType = parameterizedType.getRawType();

            if (rawType instanceof Class<?> rawClass) {
                if (List.class.isAssignableFrom(rawClass)) {
                    if (!(raw instanceof List<?> rawList)) {
                        return List.of();
                    }

                    Type itemType = parameterizedType.getActualTypeArguments()[0];
                    List<Object> converted = new ArrayList<>();
                    for (Object item : rawList) {
                        converted.add(convert(item, itemType));
                    }
                    return converted;
                }

                if (Map.class.isAssignableFrom(rawClass)) {
                    return raw;
                }

                if (Optional.class.isAssignableFrom(rawClass)) {
                    Type innerType = parameterizedType.getActualTypeArguments()[0];
                    return Optional.ofNullable(convert(raw, innerType));
                }

                if (raw instanceof Map<?, ?> rawMap) {
                    return mapToPojo((Map<String, Object>) rawMap, rawClass);
                }
            }
        }

        return raw;
    }

    private static String resolveParameterName(Parameter parameter) {
        JsonProperty jsonProperty = parameter.getAnnotation(JsonProperty.class);
        if (jsonProperty != null && jsonProperty.value() != null && !jsonProperty.value().isBlank()) {
            return jsonProperty.value();
        }
        return parameter.getName();
    }

    private static <T> T mapToPojo(Map<String, Object> map, Class<T> clazz) {
        try {
            Constructor<T> constructor = clazz.getDeclaredConstructor();
            constructor.setAccessible(true);
            T instance = constructor.newInstance();

            for (Field field : getAllInstanceFields(clazz)) {
                field.setAccessible(true);

                String inputFieldName = resolveFieldName(field);
                Object raw = map.get(inputFieldName);
                Object converted = convert(raw, field.getGenericType());
                field.set(instance, converted);
            }

            return instance;
        } catch (Exception e) {
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR,
                "Failed to map arguments to DTO: " + clazz.getSimpleName());
        }
    }
}
