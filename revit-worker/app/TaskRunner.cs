using System.Diagnostics;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace RevitWorkerApp;

/// <summary>
/// C# port of tasks.py -- executes Revit/PyRevit/PowerShell commands as subprocesses.
/// </summary>
public static class TaskRunner
{
    private static readonly HashSet<string> AllowedCommandTypes = new(StringComparer.OrdinalIgnoreCase)
        { "pyrevit", "rtv", "powershell" };

    private const int MaxOutputBytes = 64 * 1024;

    public static async Task<Dictionary<string, object?>> RunRevitCommand(Dictionary<string, object?> jobData)
    {
        var jobId = GetString(jobData, "job_id");
        var commandType = GetString(jobData, "command_type")?.ToLowerInvariant() ?? "";
        var scriptPath = GetString(jobData, "script_path") ?? "";
        var modelPath = GetString(jobData, "model_path");
        var revitVersion = GetString(jobData, "revit_version");
        var batchFile = GetString(jobData, "batch_file");
        var arguments = GetStringList(jobData, "arguments");
        var timeoutSeconds = GetInt(jobData, "timeout_seconds", 3600);
        var workingDirectory = GetString(jobData, "working_directory");

        var startedAt = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ");

        if (!AllowedCommandTypes.Contains(commandType))
        {
            return new Dictionary<string, object?>
            {
                ["success"] = false,
                ["error"] = $"Invalid command_type '{commandType}'. Must be one of: pyrevit, rtv, powershell",
                ["exit_code"] = null,
                ["started_at"] = startedAt,
                ["finished_at"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ"),
            };
        }

        List<string> cmd;
        try
        {
            cmd = BuildCommand(commandType, scriptPath, arguments, modelPath, revitVersion, batchFile);
        }
        catch (Exception ex)
        {
            return new Dictionary<string, object?>
            {
                ["success"] = false,
                ["error"] = ex.Message,
                ["exit_code"] = null,
                ["started_at"] = startedAt,
                ["finished_at"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ"),
            };
        }

        var cwd = !string.IsNullOrEmpty(workingDirectory) && Directory.Exists(workingDirectory)
            ? workingDirectory
            : null;

        Process? process = null;
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = cmd[0],
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            for (var i = 1; i < cmd.Count; i++)
                psi.ArgumentList.Add(cmd[i]);
            if (cwd != null)
                psi.WorkingDirectory = cwd;

            AppLog.Info($"[TaskRunner] Launching: {psi.FileName} {string.Join(" ", cmd.Skip(1))}");
            process = Process.Start(psi);
            if (process == null)
                throw new InvalidOperationException("Failed to start process");
            AppLog.Info($"[TaskRunner] Process started PID={process.Id}, waiting up to {timeoutSeconds}s...");

            var stdoutTask = process.StandardOutput.ReadToEndAsync();
            var stderrTask = process.StandardError.ReadToEndAsync();

            var exited = process.WaitForExit(timeoutSeconds * 1000);
            if (!exited)
            {
                try { process.Kill(entireProcessTree: true); } catch { }
                return new Dictionary<string, object?>
                {
                    ["success"] = false,
                    ["error"] = $"Process timed out after {timeoutSeconds} seconds and was killed",
                    ["exit_code"] = null,
                    ["started_at"] = startedAt,
                    ["finished_at"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ"),
                };
            }

            var rawStdout = stdoutTask.GetAwaiter().GetResult();
            var stderr = Truncate(stderrTask.GetAwaiter().GetResult());
            var exitCode = process.ExitCode;
            var finishedAt = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ");

            // Parse sentinel data from stdout and merge into result
            var baseResult = new Dictionary<string, object?>
            {
                ["success"] = exitCode == 0,
                ["exit_code"] = exitCode,
                ["started_at"] = startedAt,
                ["finished_at"] = finishedAt,
                ["error"] = exitCode != 0 ? $"Process exited with code {exitCode}" : null,
            };

            var (parsedResult, cleanedStdout) = ParseSentinel(baseResult, rawStdout);

            // Upload log files (use raw stdout/stderr locally for log discovery, but don't store them in the result)
            var startedAtParsed = DateTime.Parse(startedAt);
            var finishedAtParsed = DateTime.Parse(finishedAt);
            var logFiles = FindLogFiles(commandType, startedAtParsed, finishedAtParsed, process?.Id ?? 0, jobId);

            var stdoutLogs = FindLogsFromStdout(cleanedStdout, startedAtParsed, finishedAtParsed);
            foreach (var kv in stdoutLogs)
                logFiles.TryAdd(kv.Key, kv.Value);

            var settings = AppSettings.Load();
            var workerLogTemp = logFiles.GetValueOrDefault("worker");
            var uploadedLogPaths = await UploadLogs(jobId ?? "", settings.ApiGatewayUrl, settings.ApiKey, logFiles);

            // Clean up temp worker log extract
            if (workerLogTemp != null && workerLogTemp.StartsWith(Path.GetTempPath()))
                try { File.Delete(workerLogTemp); } catch { }

            if (uploadedLogPaths.Count > 0)
            {
                parsedResult["log_files"] = uploadedLogPaths;
            }

            return parsedResult;
        }
        catch (System.ComponentModel.Win32Exception ex)
        {
            return new Dictionary<string, object?>
            {
                ["success"] = false,
                ["error"] = $"Executable not found: {ex.Message}",
                ["exit_code"] = null,
                ["started_at"] = startedAt,
                ["finished_at"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ"),
            };
        }
        catch (Exception ex)
        {
            return new Dictionary<string, object?>
            {
                ["success"] = false,
                ["error"] = $"Unexpected error: {ex.Message}",
                ["exit_code"] = null,
                ["started_at"] = startedAt,
                ["finished_at"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ"),
            };
        }
        finally
        {
            process?.Dispose();
        }
    }

    /// <summary>
    /// Scans stdout for RW_RESULT: lines, merges structured JSON data into result, and strips sentinel lines from stdout.
    /// </summary>
    internal static (Dictionary<string, object?> result, string cleanedStdout) ParseSentinel(Dictionary<string, object?> baseResult, string stdout)
    {
        var result = new Dictionary<string, object?>(baseResult);
        var lines = stdout.Split('\n');
        var cleanedLines = new List<string>();

        foreach (var line in lines)
        {
            var trimmedLine = line.Trim();
            if (trimmedLine.StartsWith("RW_RESULT:", StringComparison.Ordinal))
            {
                // Extract JSON part after the prefix
                var jsonPart = trimmedLine.Substring("RW_RESULT:".Length).Trim();
                if (!string.IsNullOrEmpty(jsonPart))
                {
                    try
                    {
                        using var doc = JsonDocument.Parse(jsonPart);
                        var root = doc.RootElement;
                        if (root.ValueKind == JsonValueKind.Object)
                        {
                            foreach (var prop in root.EnumerateObject())
                            {
                                result[prop.Name] = JsonElementToObject(prop.Value);
                            }
                        }
                    }
                    catch (JsonException)
                    {
                        // Malformed JSON in sentinel line - skip silently
                        AppLog.Warning($"[TaskRunner] Malformed JSON in RW_RESULT line: {jsonPart}");
                    }
                }
            }
            else
            {
                // Keep non-sentinel lines
                cleanedLines.Add(line);
            }
        }

        var cleanedStdout = string.Join('\n', cleanedLines);
        return (result, cleanedStdout);
    }

    private static object? JsonElementToObject(JsonElement el)
    {
        return el.ValueKind switch
        {
            JsonValueKind.String => el.GetString(),
            JsonValueKind.Number => el.TryGetInt64(out var i) ? i : el.GetDouble(),
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.Null => null,
            _ => el.GetRawText()
        };
    }

    /// <summary>
    /// Finds all relational log files for this job run (journal, pyrevit, rtv, worker)
    /// based on time window and optional PID filtering. Always searches for all log types.
    /// </summary>
    internal static Dictionary<string, string?> FindLogFiles(string commandType, DateTime startedAt, DateTime finishedAt, int processId, string? jobId = null)
    {
        var logFiles = new Dictionary<string, string?>
        {
            ["journal"] = FindJournalLog(startedAt, finishedAt),
            ["pyrevit"] = FindPyRevitLog(startedAt, finishedAt, processId),
            ["rtv"] = FindRtvLog(startedAt, finishedAt),
            ["worker"] = FindWorkerLog(jobId)
        };

        return logFiles;
    }

    /// <summary>
    /// Finds the most recent journal file within the time window.
    /// </summary>
    private static string? FindJournalLog(DateTime startedAt, DateTime finishedAt)
    {
        try
        {
            var journalsDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Autodesk", "Revit", "Autodesk Revit 20*",
                "Journals"
            );

            // Find all version directories matching 20*
            var versionDirs = Directory.GetDirectories(
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Autodesk", "Revit"),
                "Autodesk Revit 20*"
            );

            string? bestFile = null;
            DateTime bestTime = DateTime.MinValue;

            foreach (var versionDir in versionDirs)
            {
                var journalsPath = Path.Combine(versionDir, "Journals");
                if (!Directory.Exists(journalsPath)) continue;

                var journalFiles = Directory.GetFiles(journalsPath, "*.txt");
                foreach (var file in journalFiles)
                {
                    var writeTime = File.GetLastWriteTimeUtc(file);
                    if (writeTime >= startedAt.AddSeconds(-10) && writeTime <= finishedAt.AddSeconds(60))
                    {
                        if (writeTime > bestTime)
                        {
                            bestTime = writeTime;
                            bestFile = file;
                        }
                    }
                }
            }

            return bestFile;
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// Finds the PyRevit log file that matches the time window and preferably contains the process ID.
    /// </summary>
    private static string? FindPyRevitLog(DateTime startedAt, DateTime finishedAt, int processId)
    {
        try
        {
            var pyrevitDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "pyRevit"
            );

            // Find all version directories matching 20*
            var versionDirs = Directory.GetDirectories(pyrevitDir, "20*");

            string? bestFile = null;
            DateTime bestTime = DateTime.MinValue;

            foreach (var versionDir in versionDirs)
            {
                if (!Directory.Exists(versionDir)) continue;

                var logFiles = Directory.GetFiles(versionDir, "*.log");
                foreach (var file in logFiles)
                {
                    var writeTime = File.GetLastWriteTimeUtc(file);
                    if (writeTime >= startedAt.AddSeconds(-10) && writeTime <= finishedAt.AddSeconds(60))
                    {
                        var fileName = Path.GetFileName(file);
                        if (fileName.Contains(processId.ToString()))
                        {
                            // Perfect match - contains PID
                            return file;
                        }

                        if (writeTime > bestTime)
                        {
                            bestTime = writeTime;
                            bestFile = file;
                        }
                    }
                }
            }

            return bestFile;
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// Finds the RTV schedule log file within the time window.
    /// </summary>
    private static string? FindRtvLog(DateTime startedAt, DateTime finishedAt)
    {
        try
        {
            var rtvDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "RTV Tools", "Xporter Pro 20*", "R1.0", "enu", "Schedule Logs"
            );

            // Find all version directories matching 20*
            var versionDirs = Directory.GetDirectories(
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "RTV Tools"),
                "Xporter Pro 20*"
            );

            string? bestFile = null;
            DateTime bestTime = DateTime.MinValue;

            foreach (var versionDir in versionDirs)
            {
                var logsPath = Path.Combine(versionDir, "R1.0", "enu", "Schedule Logs");
                if (!Directory.Exists(logsPath)) continue;

                var logFiles = Directory.GetFiles(logsPath);
                foreach (var file in logFiles)
                {
                    var writeTime = File.GetLastWriteTimeUtc(file);
                    if (writeTime >= startedAt.AddSeconds(-10) && writeTime <= finishedAt.AddSeconds(60))
                    {
                        if (writeTime > bestTime)
                        {
                            bestTime = writeTime;
                            bestFile = file;
                        }
                    }
                }
            }

            return bestFile;
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// Extracts lines from the worker log that mention the given job ID
    /// and writes them to a temp file. Returns the temp file path.
    /// </summary>
    private static string? FindWorkerLog(string? jobId)
    {
        if (string.IsNullOrEmpty(jobId)) return null;
        var logPath = AppLog.LogFilePath;
        if (logPath == null || !File.Exists(logPath)) return null;

        try
        {
            var relevantLines = new List<string>();
            foreach (var line in File.ReadLines(logPath))
            {
                if (line.Contains(jobId))
                    relevantLines.Add(line);
            }

            if (relevantLines.Count == 0) return null;

            var tempPath = Path.Combine(Path.GetTempPath(), $"revit-worker-{jobId}.log");
            File.WriteAllLines(tempPath, relevantLines);
            return tempPath;
        }
        catch
        {
            return null;
        }
    }

    private static readonly System.Text.RegularExpressions.Regex QuotedFilePathRegex =
        new(@"""([A-Za-z]:\\[^""]+\.(log|txt))""", System.Text.RegularExpressions.RegexOptions.IgnoreCase);

    // Matches unquoted directory paths on lines like "Logs: C:\path\to\dir\"
    private static readonly System.Text.RegularExpressions.Regex LogDirLineRegex =
        new(@"(?:^|\n)[^\n]*\bLogs?:\s*([A-Za-z]:\\[^\n""]+\\)", System.Text.RegularExpressions.RegexOptions.IgnoreCase);

    // Matches RTV "Schedule XML: C:\...\RTVXporter_Schedule_<timestamp>.xml" lines
    private static readonly System.Text.RegularExpressions.Regex RtvScheduleXmlRegex =
        new(@"Schedule XML:\s*[^\n]*\\(RTVXporter_Schedule_(\d{8}_\d{6}))\.xml", System.Text.RegularExpressions.RegexOptions.IgnoreCase);

    /// <summary>
    /// Scans stdout for:
    ///   1. Quoted file paths ending in .log or .txt that exist on disk.
    ///   2. Unquoted directory paths on "Logs: <dir>\" lines — scans those dirs
    ///      and picks any .log/.txt files modified within the job's time window.
    ///   3. RTV "Schedule XML: ...\RTVXporter_Schedule_<timestamp>.xml" lines —
    ///      looks for the corresponding log in the standard RTV schedule logs dir.
    /// </summary>
    internal static Dictionary<string, string?> FindLogsFromStdout(string stdout, DateTime startedAt, DateTime finishedAt)
    {
        var found = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);

        // 1. Quoted file paths
        foreach (System.Text.RegularExpressions.Match m in QuotedFilePathRegex.Matches(stdout))
        {
            var path = m.Groups[1].Value;
            if (File.Exists(path))
                found.TryAdd(Path.GetFileNameWithoutExtension(path), path);
        }

        // 2. Unquoted log directory paths
        foreach (System.Text.RegularExpressions.Match m in LogDirLineRegex.Matches(stdout))
        {
            var dir = m.Groups[1].Value.TrimEnd('\\', '/');
            if (!Directory.Exists(dir)) continue;

            try
            {
                foreach (var file in Directory.GetFiles(dir, "*.log").Concat(Directory.GetFiles(dir, "*.txt")))
                {
                    var writeTime = File.GetLastWriteTimeUtc(file);
                    if (writeTime >= startedAt.AddSeconds(-10) && writeTime <= finishedAt.AddSeconds(120))
                        found.TryAdd(Path.GetFileNameWithoutExtension(file), file);
                }
            }
            catch { }
        }

        // 3. RTV schedule log: derive from the "Schedule XML:" timestamp in stdout
        foreach (System.Text.RegularExpressions.Match m in RtvScheduleXmlRegex.Matches(stdout))
        {
            var timestamp = m.Groups[2].Value; // e.g. "20260302_210329"
            try
            {
                var rtvBase = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "RTV Tools");
                if (!Directory.Exists(rtvBase)) continue;

                foreach (var versionDir in Directory.GetDirectories(rtvBase, "Xporter Pro 20*"))
                {
                    var logsDir = Path.Combine(versionDir, "R1.0", "enu", "Schedule Logs");
                    if (!Directory.Exists(logsDir)) continue;

                    foreach (var file in Directory.GetFiles(logsDir, $"*{timestamp}*.log"))
                        found.TryAdd(Path.GetFileNameWithoutExtension(file), file);
                }
            }
            catch { }
        }

        return found;
    }

    /// <summary>
    /// Uploads log files to the API gateway and returns their paths.
    /// </summary>
    internal static async Task<List<string>> UploadLogs(string jobId, string? apiGatewayUrl, string? apiKey, Dictionary<string, string?> logFiles)
    {
        var uploadedPaths = new List<string>();

        if (string.IsNullOrEmpty(apiGatewayUrl) || string.IsNullOrEmpty(apiKey))
        {
            AppLog.Info("[TaskRunner] Skipping log upload - API gateway URL or key not configured");
            return uploadedPaths;
        }

        using var httpClient = new HttpClient();
        httpClient.DefaultRequestHeaders.Add("X-API-Key", apiKey);

        foreach (var kvp in logFiles)
        {
            var logType = kvp.Key;
            var filePath = kvp.Value;

            if (string.IsNullOrEmpty(filePath) || !File.Exists(filePath))
                continue;

            try
            {
                using var content = new MultipartFormDataContent();
                content.Add(new StringContent(jobId), "job_id");
                content.Add(new StringContent(logType), "log_type");

                var fileContent = new ByteArrayContent(await File.ReadAllBytesAsync(filePath));
                fileContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("application/octet-stream");
                content.Add(fileContent, "file", Path.GetFileName(filePath));

                var uploadUrl = $"{apiGatewayUrl.TrimEnd('/')}/revit/logs";
                var response = await httpClient.PostAsync(uploadUrl, content);

                if (response.IsSuccessStatusCode)
                {
                    var responseJson = await response.Content.ReadAsStringAsync();
                    var responseData = JsonSerializer.Deserialize<Dictionary<string, object?>>(responseJson);
                    if (responseData != null && responseData.TryGetValue("file_path", out var pathObj))
                    {
                        uploadedPaths.Add(pathObj?.ToString() ?? "");
                        AppLog.Info($"[TaskRunner] Uploaded {logType} log to {pathObj}");
                    }
                }
                else
                {
                    AppLog.Warning($"[TaskRunner] Failed to upload {logType} log: {response.StatusCode}");
                }
            }
            catch (Exception ex)
            {
                AppLog.Warning($"[TaskRunner] Error uploading {logType} log: {ex.Message}");
            }
        }

        return uploadedPaths;
    }

    private static List<string> BuildCommand(string commandType, string scriptPath,
        List<string> arguments, string? modelPath, string? revitVersion, string? batchFile)
    {
        var cmd = new List<string>();
        switch (commandType)
        {
            case "pyrevit":
                cmd.AddRange(["pyrevit", "run", scriptPath]);
                if (!string.IsNullOrEmpty(modelPath)) cmd.Add(modelPath);
                if (!string.IsNullOrEmpty(revitVersion)) cmd.Add($"--revit={revitVersion}");
                cmd.AddRange(arguments);
                break;
            case "rtv":
                cmd.AddRange(["powershell.exe", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-File", scriptPath]);
                if (!string.IsNullOrEmpty(batchFile)) cmd.AddRange(["-BatchFile", batchFile]);
                cmd.AddRange(arguments);
                break;
            case "powershell":
                cmd.AddRange(["powershell.exe", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-File", scriptPath]);
                if (!string.IsNullOrEmpty(modelPath)) cmd.AddRange(["-ModelPath", modelPath]);
                if (!string.IsNullOrEmpty(revitVersion)) cmd.AddRange(["-RevitVersion", revitVersion]);
                cmd.AddRange(arguments);
                break;
            default:
                throw new ArgumentException($"Unknown command_type: {commandType}");
        }
        return cmd;
    }

    private static string Truncate(string text)
    {
        var bytes = Encoding.UTF8.GetBytes(text);
        if (bytes.Length <= MaxOutputBytes) return text;
        return "[...truncated...]\n" + Encoding.UTF8.GetString(bytes, bytes.Length - MaxOutputBytes, MaxOutputBytes);
    }

    private static string? GetString(Dictionary<string, object?> dict, string key)
    {
        return dict.TryGetValue(key, out var val) ? val?.ToString() : null;
    }

    private static int GetInt(Dictionary<string, object?> dict, string key, int defaultValue)
    {
        if (!dict.TryGetValue(key, out var val) || val == null) return defaultValue;
        if (val is int i) return i;
        if (val is long l) return (int)l;
        if (val is double d) return (int)d;
        if (int.TryParse(val.ToString(), out var parsed)) return parsed;
        return defaultValue;
    }

    private static List<string> GetStringList(Dictionary<string, object?> dict, string key)
    {
        if (!dict.TryGetValue(key, out var val) || val == null) return [];
        if (val is object[] arr) return arr.Select(o => o?.ToString() ?? "").ToList();
        if (val is System.Collections.ArrayList list) return list.Cast<object>().Select(o => o?.ToString() ?? "").ToList();
        return [];
    }
}
