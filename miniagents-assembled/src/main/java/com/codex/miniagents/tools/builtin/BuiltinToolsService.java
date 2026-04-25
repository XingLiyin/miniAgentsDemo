package com.codex.miniagents.tools.builtin;

import com.codex.miniagents.config.MiniAgentsProperties;
import com.codex.miniagents.domain.model.task.Task;
import com.codex.miniagents.domain.service.TaskService;
import com.codex.miniagents.exception.AppException;
import com.codex.miniagents.exception.ErrorCode;
import com.codex.miniagents.skills.SkillLoader;
import com.codex.miniagents.skills.SkillMcpConn;
import com.codex.miniagents.skills.SkillRegistry;
import com.codex.miniagents.skills.model.SkillMetadata;
import com.codex.miniagents.tools.ToolStoreClient;
import com.codex.miniagents.tools.annotation.ToolParam;
import com.codex.miniagents.tools.annotation.ToolSpec;
import com.codex.miniagents.tools.model.CallContext;
import com.codex.miniagents.tools.model.ToolResult;
import com.codex.miniagents.dto.response.SearchResult;
import com.fasterxml.jackson.annotation.JsonProperty;

import lombok.RequiredArgsConstructor;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClientException;

import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.nio.charset.Charset;
import java.nio.file.FileSystem;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

@Component
@RequiredArgsConstructor
public class BuiltinToolsService {
    private static final List<Pattern> BASH_BLACKLIST = List.of(Pattern.compile("\\brm\\s+-rf\\b"),
        Pattern.compile("\\bmkfs\\b"), Pattern.compile("\\bdd\\b.*\\bof=/dev/"), Pattern.compile("\\bshutdown\\b"),
        Pattern.compile("\\breboot\\b"), Pattern.compile("\\bsudo\\b"), Pattern.compile("\\bsu\\b\\s"),
        Pattern.compile("\\bchmod\\s+777\\b"), Pattern.compile("\\bcurl\\b.*\\|\\s*bash"),
        Pattern.compile("\\bwget\\b.*\\|\\s*bash"));

    private static final Pattern SSRF_BLOCKED = Pattern.compile(
        "^(localhost|127\\.\\d+\\.\\d+\\.\\d+|10\\.\\d+\\.\\d+\\.\\d+|"
            + "192\\.168\\.\\d+\\.\\d+|172\\.(1[6-9]|2\\d|3[01])\\.\\d+\\.\\d+)$", Pattern.CASE_INSENSITIVE);

    private final MiniAgentsProperties properties;

    private final ToolStoreClient toolStoreClient;

    private final BuiltinHttpToolExecutor builtinHttpToolExecutor;

    private final SkillRegistry skillRegistry;

    private final SkillLoader skillLoader;

    private final TaskService taskService;

    @ToolSpec(name = "bash_exec",
        description = "Execute a shell command in a restricted environment. Returns stdout+stderr. Non-zero exit code sets is_error=true.")
    public ToolResult bashExec(@ToolParam("Shell command to execute") String command, CallContext ctx) {
        for (Pattern pattern : BASH_BLACKLIST) {
            if (pattern.matcher(command).find()) {
                throw new AppException(ErrorCode.TOOL_COMMAND_BLOCKED,
                    "Command blocked by blacklist: " + pattern.pattern());
            }
        }

        double timeoutSec = properties.getBashExecTimeoutMs() / 1000.0;

        try {
            ProcessBuilder processBuilder = new ProcessBuilder(buildShellCommand(command)).redirectErrorStream(false);
            String cwd = resolveWorkingDir(ctx);
            if (cwd != null && !cwd.isBlank()) {
                processBuilder.directory(Path.of(cwd).toAbsolutePath().normalize().toFile());
            }
            Process process = processBuilder.start();

            boolean finished = process.waitFor(properties.getBashExecTimeoutMs(),
                java.util.concurrent.TimeUnit.MILLISECONDS);
            if (!finished) {
                process.destroyForcibly();
                throw new AppException(ErrorCode.TOOL_TIMEOUT, "bash_exec timed out after " + timeoutSec + "s");
            }

            String stdout = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            String stderr = new String(process.getErrorStream().readAllBytes(), StandardCharsets.UTF_8);
            String output = stdout + stderr;

            int limit = properties.getBashExecOutputLimitBytes();
            byte[] bytes = output.getBytes(StandardCharsets.UTF_8);
            if (bytes.length > limit) {
                output = new String(bytes, 0, limit, StandardCharsets.UTF_8) + "\n[output truncated at " + limit
                    + " bytes]";
            }

            boolean isError = process.exitValue() != 0;
            return ToolResult.builder()
                .content(output)
                .isError(isError)
                .errorCode(isError ? ErrorCode.BASH_NONZERO_EXIT.name() : null)
                .metadata(Map.of("exit_code", process.exitValue(), "cwd", cwd == null ? "" : cwd))
                .build();
        } catch (IOException e) {
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, e.getMessage());
        }
    }

    @ToolSpec(name = "http_request",
        description = "Make an HTTP request to an external URL. Private/localhost addresses are blocked.")
    public ToolResult httpRequest(@ToolParam("Target URL (must be a public address)") String url,
        @ToolParam(value = "HTTP method: GET, POST, PUT, DELETE, PATCH", required = false) String method,
        @ToolParam(value = "Optional HTTP headers as key-value pairs", required = false) Map<String, Object> headers,
        @ToolParam(value = "Optional request body (string)", required = false) String body) {
        String actualMethod = method == null || method.isBlank() ? "GET" : method;
        Map<String, Object> actualHeaders = headers == null ? Map.of() : headers;

        String host;
        try {
            URI uri = new URI(url);
            host = uri.getHost();
            if (host == null || host.isBlank()) {
                throw new AppException(ErrorCode.INVALID_ARGUMENT, "http_request: invalid URL format");
            }
        } catch (URISyntaxException e) {
            throw new AppException(ErrorCode.INVALID_ARGUMENT, "http_request: invalid URL format");
        }

        if (SSRF_BLOCKED.matcher(host).matches()) {
            throw new AppException(ErrorCode.SSRF_BLOCKED, "Access to host '" + host + "' is not allowed");
        }

        try {
            BuiltinHttpToolExecutor.HttpToolResponse response = builtinHttpToolExecutor.execute(url, actualMethod,
                actualHeaders, body, properties.getHttpRequestTimeoutMs());

            String content = response.body();
            int limit = properties.getHttpResponseLimitBytes();
            byte[] bytes = content.getBytes(StandardCharsets.UTF_8);
            if (bytes.length > limit) {
                content = new String(bytes, 0, limit, StandardCharsets.UTF_8) + "\n[response truncated at " + limit
                    + " bytes]";
            }

            boolean isError = response.statusCode() >= 400;
            return ToolResult.builder()
                .content(content)
                .isError(isError)
                .errorCode(isError ? "HTTP_" + response.statusCode() : null)
                .metadata(Map.of("status_code", response.statusCode()))
                .build();
        } catch (BuiltinHttpToolExecutor.HttpToolTimeoutException e) {
            double timeoutSec = properties.getHttpRequestTimeoutMs() / 1000.0;
            throw new AppException(ErrorCode.TOOL_TIMEOUT, "http_request timed out after " + timeoutSec + "s");
        } catch (RestClientException e) {
            throw new AppException(ErrorCode.HTTP_REQUEST_ERROR, e.getMessage());
        }
    }

    @ToolSpec(name = "search_tools",
        description = "Search registered tools by semantic relevance using the external tool store. Returns a JSON list of matching tools with name, description, and relevance score. Requires MINIAGENTS_TOOL_STORE_BASE_URL to be configured.")
    public ToolResult searchTools(
        @ToolParam("Natural language description of what you want to accomplish") String query,
        @ToolParam(value = "Maximum number of tools to return (default 5)", required = false) Integer topK) {
        int actualTopK = topK == null ? 5 : topK;

        if (!toolStoreClient.isEnabled()) {
            return ToolResult.builder()
                .content("Tool store is not configured. Set MINIAGENTS_TOOL_STORE_BASE_URL to enable semantic search.")
                .isError(true)
                .errorCode(ErrorCode.TOOL_STORE_NOT_CONFIGURED.name())
                .build();
        }

        List<SearchResult> results = toolStoreClient.search(query, actualTopK);
        if (results.isEmpty()) {
            return ToolResult.builder().content("[]").build();
        }

        String json = toPrettyJson(results);
        return ToolResult.builder().content(json).build();
    }

    @ToolSpec(name = "read",
        description = "Read a file from the filesystem and return its content as text.")
    public ToolResult read(@ToolParam("Absolute or relative path to the file to read") String path,
        @ToolParam(value = "File encoding (default: utf-8)", required = false) String encoding,
        CallContext ctx) {
        String actualEncoding = encoding == null || encoding.isBlank() ? "utf-8" : encoding;
        Path filePath = resolvePath(path, ctx);

        if (!Files.exists(filePath)) {
            throw new AppException(ErrorCode.FILE_NOT_FOUND, "File not found: " + path);
        }
        if (!Files.isRegularFile(filePath)) {
            throw new AppException(ErrorCode.NOT_A_FILE, "Path is not a file: " + path);
        }

        long size;
        try {
            size = Files.size(filePath);
        } catch (IOException e) {
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, "Failed to inspect file size: " + path);
        }

        int limit = properties.getHttpResponseLimitBytes();
        if (size > limit) {
            throw new AppException(ErrorCode.FILE_TOO_LARGE,
                "File size " + size + " bytes exceeds limit " + limit + " bytes");
        }

        try {
            Charset charset = Charset.forName(actualEncoding);
            String content = Files.readString(filePath, charset);
            return ToolResult.builder()
                .content(content)
                .metadata(Map.of("path", filePath.toAbsolutePath().toString(), "size", size))
                .build();
        } catch (IllegalArgumentException e) {
            throw new AppException(ErrorCode.DECODE_ERROR,
                "Failed to decode file with encoding '" + actualEncoding + "': " + e.getMessage());
        } catch (IOException e) {
            throw new AppException(ErrorCode.DECODE_ERROR,
                "Failed to decode file with encoding '" + actualEncoding + "': " + e.getMessage());
        }
    }

    @ToolSpec(name = "write",
        description = "Write text content to a file. Creates parent directories if they don't exist.")
    public ToolResult write(@ToolParam("Absolute or relative path to the file to write") String path,
        @ToolParam("Text content to write to the file") String content,
        @ToolParam(value = "File encoding (default: utf-8)", required = false) String encoding,
        @ToolParam(value = "Allow overwriting an existing file (default: true)", required = false) Boolean overwrite,
        CallContext ctx) {
        String actualEncoding = encoding == null || encoding.isBlank() ? "utf-8" : encoding;
        boolean actualOverwrite = overwrite == null || overwrite;
        String actualContent = content == null ? "" : content;
        Path filePath = resolvePath(path, ctx);

        if (Files.exists(filePath) && !actualOverwrite) {
            throw new AppException(ErrorCode.FILE_EXISTS, "File already exists and overwrite=false: " + path);
        }

        try {
            Path parent = filePath.getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            Charset charset = Charset.forName(actualEncoding);
            Files.writeString(filePath, actualContent, charset);
        } catch (IllegalArgumentException e) {
            throw new AppException(ErrorCode.WRITE_ERROR,
                "Failed to write file: invalid encoding '" + actualEncoding + "'");
        } catch (IOException e) {
            throw new AppException(ErrorCode.WRITE_ERROR, "Failed to write file: " + e.getMessage());
        }

        long bytes = actualContent.getBytes(Charset.forName(actualEncoding)).length;
        return ToolResult.builder()
            .content("Written " + bytes + " bytes to " + filePath.toAbsolutePath())
            .metadata(Map.of("path", filePath.toAbsolutePath().toString(), "bytes", bytes))
            .build();
    }

    @ToolSpec(name = "glob",
        description = "Find files matching a glob pattern. Returns a newline-separated list of matching paths.")
    public ToolResult glob(@ToolParam("Glob pattern to match files, e.g. 'src/**/*.py'") String pattern,
        @ToolParam(value = "Root directory to search from (default: current working directory)", required = false) String root,
        @ToolParam(value = "Maximum number of results to return (default: 200)", required = false) Integer limit,
        CallContext ctx) {
        String actualRoot = root == null || root.isBlank() ? "." : root;
        int actualLimit = limit == null ? 200 : limit;

        Path rootPath = resolvePath(actualRoot, ctx).toAbsolutePath().normalize();
        if (!Files.isDirectory(rootPath)) {
            throw new AppException(ErrorCode.NOT_A_DIRECTORY, "Root path is not a directory: " + actualRoot);
        }

        List<String> matches;
        try {
            FileSystem fs = FileSystems.getDefault();
            var matcher = fs.getPathMatcher("glob:" + pattern);
            try (var stream = Files.walk(rootPath)) {
                matches = stream
                    .filter(Files::isRegularFile)
                    .map(rootPath::relativize)
                    .map(Path::toString)
                    .filter(rel -> matcher.matches(Path.of(rel)))
                    .sorted()
                    .toList();
            }
        } catch (IOException e) {
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, "Failed to glob files: " + e.getMessage());
        }

        boolean truncated = false;
        if (matches.size() > actualLimit) {
            matches = matches.subList(0, actualLimit);
            truncated = true;
        }

        String output = matches.isEmpty() ? "(no matches)" : String.join("\n", matches);
        if (truncated) {
            output += "\n[truncated at " + actualLimit + " results]";
        }

        return ToolResult.builder()
            .content(output)
            .metadata(Map.of("count", matches.size(), "truncated", truncated, "root", rootPath.toString()))
            .build();
    }

    @ToolSpec(name = "load_skill_reference",
        description = "Read a reference file from the current task's skill directory. Use the relative path as shown in the skill instructions (e.g. 'references/background.md', 'checks/self_check.md').")
    public ToolResult loadSkillReference(
        @JsonProperty("reference_path")
        @ToolParam("Relative path to the reference file within the skill directory") String referencePath,
        CallContext ctx) {
        if (referencePath == null || referencePath.isBlank()) {
            throw new AppException(ErrorCode.INVALID_ARGUMENT, "reference_path is required");
        }

        String skillName = resolveSkillName(ctx, "Task has no skill assigned; cannot load skill reference");
        SkillMetadata skillMetadata = skillRegistry.getMetadata(skillName);
        if (skillMetadata == null) {
            SkillMcpConn conn = resolveRemoteSkillConn(skillName);
            try {
                String content = conn.loadSkillReference(skillName, referencePath, ctx);
                return ToolResult.builder()
                    .content(content)
                    .metadata(Map.of("skill", skillName, "path", referencePath))
                    .build();
            } catch (Exception e) {
                throw new AppException(ErrorCode.TOOL_EXEC_ERROR, e.getMessage());
            }
        }

        try {
            String content = skillLoader.loadResource(skillMetadata.getSkillDir(), referencePath);
            return ToolResult.builder()
                .content(content)
                .metadata(Map.of("skill", skillMetadata.getName(), "path", referencePath))
                .build();
        } catch (IllegalArgumentException e) {
            throw new AppException(ErrorCode.INVALID_ARGUMENT, e.getMessage());
        } catch (IOException e) {
            throw new AppException(ErrorCode.FILE_NOT_FOUND,
                "Reference '" + referencePath + "' not found in skill '" + skillMetadata.getName() + "'");
        }
    }

    @ToolSpec(name = "get_skill_files",
        description = "List files available in the current task's skill directory. For local skills, supports glob patterns (e.g. 'scripts/*.py', 'references/**'). Returns a newline-separated list of file paths (local) or file names (remote). For remote skills, also returns file IDs usable with load_skill_reference and exec_skill_script.")
    public ToolResult getSkillFiles(
        @JsonProperty("pattern")
        @ToolParam(value = "Glob pattern to match files (default: '**/*', local skills only)", required = false) String pattern,
        @JsonProperty("limit")
        @ToolParam(value = "Maximum number of results (default: 200, local skills only)", required = false) Integer limit,
        CallContext ctx) {
        String actualPattern = pattern == null || pattern.isBlank() ? "**/*" : pattern;
        int actualLimit = limit == null ? 200 : limit;
        String skillName = resolveSkillName(ctx, "Task has no skill assigned; cannot list skill files");

        SkillMetadata skillMetadata = skillRegistry.getMetadata(skillName);
        if (skillMetadata == null) {
            SkillMcpConn conn = resolveRemoteSkillConn(skillName);
            try {
                String content = conn.getSkillFiles(skillName, actualPattern, actualLimit, ctx);
                return ToolResult.builder()
                    .content(content)
                    .metadata(Map.of("skill", skillName, "source", "remote"))
                    .build();
            } catch (Exception e) {
                throw new AppException(ErrorCode.TOOL_EXEC_ERROR, e.getMessage());
            }
        }

        Path skillDir = skillMetadata.getSkillDir();
        if (skillDir == null || !Files.exists(skillDir)) {
            throw new AppException(ErrorCode.FILE_NOT_FOUND,
                "Skill directory '" + skillDir + "' does not exist");
        }

        List<String> files;
        try {
            FileSystem fs = FileSystems.getDefault();
            var matcher = fs.getPathMatcher("glob:" + actualPattern);
            try (var stream = Files.walk(skillDir)) {
                files = stream
                    .filter(Files::isRegularFile)
                    .map(skillDir::relativize)
                    .filter(path -> !hasHiddenPart(path))
                    .map(Path::toString)
                    .filter(rel -> matcher.matches(Path.of(rel)))
                    .sorted()
                    .toList();
            }
        } catch (IOException e) {
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, "Failed to list skill files: " + e.getMessage());
        }

        boolean truncated = false;
        if (files.size() > actualLimit) {
            files = files.subList(0, actualLimit);
            truncated = true;
        }

        String output = files.isEmpty() ? "(no files)" : String.join("\n", files);
        if (truncated) {
            output += "\n[truncated at " + actualLimit + " results]";
        }

        return ToolResult.builder()
            .content(output)
            .metadata(Map.of("skill", skillName, "source", "local", "count", files.size(), "truncated", truncated))
            .build();
    }

    @ToolSpec(name = "exec_skill_script",
        description = "Execute a script from the current task's skill directory. The working directory is set to the skill root. Construct args as described in the skill instructions.")
    public ToolResult execSkillScript(
        @JsonProperty("script_path")
        @ToolParam("Relative path to the script within the skill directory (e.g. 'scripts/extract.py')") String scriptPath,
        @JsonProperty("args")
        @ToolParam(value = "Command-line argument string appended after the script path", required = false) String args,
        CallContext ctx) {
        if (scriptPath == null || scriptPath.isBlank()) {
            throw new AppException(ErrorCode.INVALID_ARGUMENT, "script_path is required");
        }

        String skillName = resolveSkillName(ctx, "Task has no skill assigned; cannot execute skill script");
        SkillMetadata skillMetadata = skillRegistry.getMetadata(skillName);
        if (skillMetadata == null) {
            SkillMcpConn conn = resolveRemoteSkillConn(skillName);
            try {
                return conn.execSkillScript(skillName, scriptPath, args == null ? "" : args, ctx);
            } catch (Exception e) {
                throw new AppException(ErrorCode.TOOL_EXEC_ERROR, e.getMessage());
            }
        }

        Path skillDir = skillMetadata.getSkillDir().toAbsolutePath().normalize();
        Path resolvedScriptPath = resolveSkillScript(skillDir, scriptPath);
        Path requirements = skillDir.resolve("requirements.txt");

        String actualArgs = args == null ? "" : args.trim();
        List<String> command = new ArrayList<>();
        if (resolvedScriptPath.toString().endsWith(".py")) {
            String python = requirementsExists(requirements)
                ? ensureSkillPython(skillDir, requirements)
                : discoverPythonBinary();
            command.add(python);
            command.add(resolvedScriptPath.toString());
        } else {
            command.add(resolvedScriptPath.toString());
        }
        if (!actualArgs.isBlank()) {
            command.addAll(splitArgs(actualArgs));
        }

        for (Pattern pattern : BASH_BLACKLIST) {
            if (pattern.matcher(String.join(" ", command)).find()) {
                throw new AppException(ErrorCode.TOOL_COMMAND_BLOCKED,
                    "Command blocked by blacklist: " + pattern.pattern());
            }
        }

        double timeoutSec = properties.getBashExecTimeoutMs() / 1000.0;
        try {
            String cwd = resolveWorkingDir(ctx);
            Process process = new ProcessBuilder(command)
                .directory(Path.of(cwd == null || cwd.isBlank() ? "." : cwd).toAbsolutePath().normalize().toFile())
                .redirectErrorStream(false)
                .start();

            boolean finished = process.waitFor(properties.getBashExecTimeoutMs(),
                java.util.concurrent.TimeUnit.MILLISECONDS);
            if (!finished) {
                process.destroyForcibly();
                throw new AppException(ErrorCode.TOOL_TIMEOUT, "exec_skill_script timed out after " + timeoutSec + "s");
            }

            String stdout = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            String stderr = new String(process.getErrorStream().readAllBytes(), StandardCharsets.UTF_8);
            String output = stdout + stderr;
            int limitBytes = properties.getBashExecOutputLimitBytes();
            byte[] bytes = output.getBytes(StandardCharsets.UTF_8);
            if (bytes.length > limitBytes) {
                output = new String(bytes, 0, limitBytes, StandardCharsets.UTF_8)
                    + "\n[output truncated at " + limitBytes + " bytes]";
            }

            boolean isError = process.exitValue() != 0;
            return ToolResult.builder()
                .content(output)
                .isError(isError)
                .errorCode(isError ? "SCRIPT_NONZERO_EXIT" : null)
                .metadata(Map.of(
                    "exit_code", process.exitValue(),
                    "skill", skillName,
                    "script", scriptPath,
                    "cwd", cwd == null ? "" : cwd
                ))
                .build();
        } catch (IOException e) {
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, "Failed to launch script: " + e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, "Failed to launch script: " + e.getMessage());
        } catch (Exception e) {
            return ToolResult.builder()
                .content("Failed to launch script: " + e)
                .isError(true)
                .errorCode("SCRIPT_LAUNCH_ERROR")
                .metadata(Map.of(
                    "skill", skillName,
                    "script", scriptPath
                ))
                .build();
        }
    }

    @ToolSpec(name = "request_human_input",
        description = "Pause execution and request input from the human user. Use when you need information or a decision that only the user can provide.")
    public ToolResult requestHumanInput(@ToolParam("The question or instruction to show the user") String prompt,
        @ToolParam(value = "Optional background context for the user", required = false) String context) {
        return ToolResult.builder().content("").build();
    }

    private static List<String> buildShellCommand(String command) {
        String os = System.getProperty("os.name", "").toLowerCase();
        if (os.contains("win")) {
            return List.of("cmd.exe", "/c", command);
        }
        return List.of("/bin/sh", "-c", command);
    }

    private String resolveWorkingDir(CallContext ctx) {
        if (ctx != null && ctx.getWorkingDir() != null && !ctx.getWorkingDir().isBlank()) {
            return Path.of(ctx.getWorkingDir()).toAbsolutePath().normalize().toString();
        }
        String configured = properties.getBashExecCwd();
        if (configured == null || configured.isBlank()) {
            return "";
        }
        return Path.of(configured).toAbsolutePath().normalize().toString();
    }

    private Path resolvePath(String path, CallContext ctx) {
        Path candidate = Path.of(path);
        if (candidate.isAbsolute()) {
            return candidate.toAbsolutePath().normalize();
        }
        String cwd = resolveWorkingDir(ctx);
        if (cwd == null || cwd.isBlank()) {
            return candidate;
        }
        return Path.of(cwd).resolve(candidate).normalize();
    }

    private String resolveSkillName(CallContext ctx, String missingMessage) {
        Task task = ctx == null ? null : ctx.getTask();
        if (task == null) {
            throw new AppException(ErrorCode.INVALID_ARGUMENT, "task context is required");
        }
        String skillName = "";
        if (task.getSettings() != null && task.getSettings().get("skill_name") != null) {
            skillName = String.valueOf(task.getSettings().get("skill_name"));
        }
        if (skillName.isBlank()) {
            throw new AppException(ErrorCode.INVALID_ARGUMENT, missingMessage);
        }
        return skillName;
    }

    private SkillMcpConn resolveRemoteSkillConn(String skillName) {
        SkillMcpConn conn = skillRegistry.getConnForSkill(skillName);
        if (conn == null) {
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, "Skill '" + skillName + "' not found in registry");
        }
        return conn;
    }

    private Path resolveSkillScript(Path skillDir, String scriptPath) {
        Path normalized = skillDir.resolve(scriptPath).toAbsolutePath().normalize();
        if (!normalized.startsWith(skillDir)) {
            throw new AppException(ErrorCode.INVALID_ARGUMENT, "script_path escapes skill directory");
        }
        if (!Files.isRegularFile(normalized)) {
            throw new AppException(ErrorCode.FILE_NOT_FOUND, "Script '" + scriptPath + "' not found");
        }
        return normalized;
    }

    private boolean hasHiddenPart(Path path) {
        for (Path part : path) {
            String name = part.toString();
            if (name.startsWith(".")) {
                return true;
            }
        }
        return false;
    }

    private boolean requirementsExists(Path requirements) {
        return requirements != null && Files.isRegularFile(requirements);
    }

    private String ensureSkillPython(Path skillDir, Path requirements) {
        Path venvDir = skillDir.resolve(".venv");
        Path pythonBin = venvPython(venvDir);
        Path pipBin = venvPip(venvDir);

        if (!Files.isExecutable(pythonBin)) {
            runProcess(
                List.of(discoverPythonBinary(), "-m", "venv", venvDir.toString()),
                skillDir,
                60_000,
                "Failed to create venv"
            );
        }

        runProcess(
            List.of(pipBin.toString(), "install", "-r", requirements.toString(), "--disable-pip-version-check"),
            skillDir,
            120_000,
            "pip install failed"
        );
        return pythonBin.toString();
    }

    private Path venvPython(Path venvDir) {
        boolean windows = System.getProperty("os.name", "").toLowerCase().contains("win");
        return windows ? venvDir.resolve("Scripts").resolve("python.exe") : venvDir.resolve("bin").resolve("python");
    }

    private Path venvPip(Path venvDir) {
        boolean windows = System.getProperty("os.name", "").toLowerCase().contains("win");
        return windows ? venvDir.resolve("Scripts").resolve("pip.exe") : venvDir.resolve("bin").resolve("pip");
    }

    private String discoverPythonBinary() {
        for (String candidate : List.of("python3", "python")) {
            try {
                Process process = new ProcessBuilder(candidate, "--version").redirectErrorStream(true).start();
                boolean finished = process.waitFor(5, java.util.concurrent.TimeUnit.SECONDS);
                if (finished && process.exitValue() == 0) {
                    return candidate;
                }
            } catch (Exception ignored) {
            }
        }
        return "python";
    }

    private void runProcess(List<String> command, Path cwd, long timeoutMs, String failurePrefix) {
        try {
            Process process = new ProcessBuilder(command)
                .directory(cwd.toFile())
                .redirectErrorStream(false)
                .start();
            boolean finished = process.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS);
            if (!finished) {
                process.destroyForcibly();
                throw new AppException(ErrorCode.TOOL_TIMEOUT,
                    failurePrefix + ": timed out after " + (timeoutMs / 1000.0) + "s");
            }
            String stdout = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            String stderr = new String(process.getErrorStream().readAllBytes(), StandardCharsets.UTF_8);
            if (process.exitValue() != 0) {
                String output = (stdout + stderr).trim();
                throw new AppException(ErrorCode.TOOL_EXEC_ERROR, failurePrefix + ": " + output);
            }
        } catch (IOException e) {
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, failurePrefix + ": " + e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new AppException(ErrorCode.TOOL_EXEC_ERROR, failurePrefix + ": " + e.getMessage());
        }
    }

    private List<String> splitArgs(String args) {
        return List.of(args.trim().split("\\s+"));
    }

    private String toPrettyJson(List<SearchResult> results) {
        StringBuilder sb = new StringBuilder();
        sb.append("[\n");
        for (int i = 0; i < results.size(); i++) {
            SearchResult r = results.get(i);
            sb.append("  {\n")
                .append("    \"name\": \"")
                .append(escapeJson(r.getName()))
                .append("\",\n")
                .append("    \"description\": \"")
                .append(escapeJson(r.getDescription()))
                .append("\",\n")
                .append("    \"score\": ")
                .append(String.format(java.util.Locale.ROOT, "%.4f", r.getScore()))
                .append("\n")
                .append("  }");
            if (i < results.size() - 1) {
                sb.append(",");
            }
            sb.append("\n");
        }
        sb.append("]");
        return sb.toString();
    }

    private String escapeJson(String value) {
        if (value == null) {
            return "";
        }
        return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r");
    }
}
