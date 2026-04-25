package com.codex.miniagents.exception;

import jakarta.validation.ConstraintViolationException;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class GlobalExceptionHandler {
    @ExceptionHandler(AppException.class)
    public ResponseEntity<ApiErrorResponse> handleAppException(AppException e) {
        HttpStatus status = mapStatus(e.getCode());
        return ResponseEntity.status(status)
            .body(new ApiErrorResponse(e.getCode().name(), e.getMessage()));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ApiErrorResponse> handleIllegalArgumentException(IllegalArgumentException e) {
        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(new ApiErrorResponse("BAD_REQUEST", e.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiErrorResponse> handleMethodArgumentNotValid(MethodArgumentNotValidException e) {
        String message = e.getBindingResult()
            .getFieldErrors()
            .stream()
            .findFirst()
            .map(err -> err.getField() + ": " + err.getDefaultMessage())
            .orElse("Invalid request");

        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(new ApiErrorResponse("VALIDATION_ERROR", message));
    }

    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ApiErrorResponse> handleConstraintViolation(ConstraintViolationException e) {
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
            .body(new ApiErrorResponse("VALIDATION_ERROR", e.getMessage()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiErrorResponse> handleUnknown(Exception e) {
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
            .body(new ApiErrorResponse("INTERNAL_SERVER_ERROR", e.getMessage()));
    }

    private HttpStatus mapStatus(ErrorCode code) {
        if (code == null) {
            return HttpStatus.BAD_REQUEST;
        }

        return switch (code) {
            case SESSION_NOT_FOUND, TASK_NOT_FOUND, AGENT_NOT_FOUND, MCP_NOT_FOUND, SUMMARY_NOT_FOUND, TEMPLATE_NOT_FOUND,
                LLM_NOT_FOUND, SKILL_SOURCE_NOT_FOUND -> HttpStatus.NOT_FOUND;
            case MCP_ALREADY_EXISTS, LLM_ALREADY_EXISTS, SKILL_SOURCE_ALREADY_EXISTS -> HttpStatus.CONFLICT;
            case MCP_CONNECT_CANCELLED -> HttpStatus.BAD_GATEWAY;
            case MCP_CONNECT_TIMEOUT -> HttpStatus.GATEWAY_TIMEOUT;
            case NOT_IMPLEMENTED -> HttpStatus.NOT_IMPLEMENTED;
            case INTERNAL_ERROR -> HttpStatus.INTERNAL_SERVER_ERROR;
            default -> HttpStatus.BAD_REQUEST;
        };
    }
}
