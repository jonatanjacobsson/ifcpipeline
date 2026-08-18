using StackExchange.Redis;

namespace RevitWorkerApp;

/// <summary>
/// Manages N C# worker threads that consume from the RQ queue directly.
/// No Python dependency -- jobs are unpickled and executed natively.
/// </summary>
public sealed class WorkerProcessManager : IDisposable
{
    private readonly Dictionary<string, (Thread Thread, CancellationTokenSource Cts)> _workers = new();
    private readonly object _lock = new();
    private ConnectionMultiplexer? _connection;
    private string _queueName = AppSettings.DefaultQueueNames;
    private int _nextWorkerNumber = 1;
    private bool _disposed;

    private readonly Dictionary<string, WorkerState> _workerStates = new();

    // Start gate: ensures minimum spacing between job starts across all workers
    private readonly object _startGateLock = new();
    private DateTime _nextAllowedStart = DateTime.MinValue;
    private int _jobStartDelaySeconds = AppSettings.DefaultJobStartDelaySeconds;

    public event EventHandler<WorkerStatusEventArgs>? WorkerStatusChanged;
    public event EventHandler<JobAcceptedEventArgs>? JobAccepted;
    public event EventHandler<WorkerErrorEventArgs>? WorkerError;
    public event EventHandler? WorkerCountChanged;
    public event EventHandler<AdoptedJobEventArgs>? AdoptedJobStarted;
    public event EventHandler<AdoptedJobEventArgs>? AdoptedJobFinished;

    public int CurrentCount
    {
        get { lock (_lock) return _workers.Count(kv => kv.Value.Thread.IsAlive); }
    }

    public bool AnyBusy
    {
        get { lock (_lock) return _workerStates.Values.Any(s => s.IsBusy); }
    }

    public void Configure(string redisUrl, string queueNames, int jobStartDelaySeconds = AppSettings.DefaultJobStartDelaySeconds)
    {
        _queueName = string.IsNullOrWhiteSpace(queueNames)
            ? AppSettings.DefaultQueueNames
            : queueNames.Trim().Split(',')[0].Trim();
        _jobStartDelaySeconds = Math.Max(0, jobStartDelaySeconds);
    }

    public void SetConnection(ConnectionMultiplexer connection)
    {
        lock (_lock)
        {
            if (connection != _connection)
            {
                StopAllInternal();
                _connection = connection;
            }
        }
    }

    /// <summary>
    /// Spawn one new worker thread. Returns the worker name.
    /// </summary>
    public string? AddWorker()
    {
        lock (_lock)
        {
            if (_disposed || _connection == null || !_connection.IsConnected) return null;
            if (_workers.Count >= AppSettings.MaxWorkerCount) return null;

            CleanupDeadWorkers();
            var num = _nextWorkerNumber++;
            var workerName = $"{Environment.MachineName}.w{num}";
            var cts = new CancellationTokenSource();
            var thread = new Thread(() => WorkerLoop(workerName, cts.Token))
            {
                IsBackground = true,
                Name = $"RevitWorker-{num}"
            };
            _workers[workerName] = (thread, cts);
            thread.Start();
            WorkerCountChanged?.Invoke(this, EventArgs.Empty);
            return workerName;
        }
    }

    /// <summary>
    /// Stop a specific worker by name.
    /// </summary>
    public void StopWorker(string workerName)
    {
        lock (_lock)
        {
            if (_workers.TryGetValue(workerName, out var entry))
            {
                entry.Cts.Cancel();
                _workers.Remove(workerName);
                _workerStates.Remove(workerName);
            }
        }
        WorkerStatusChanged?.Invoke(this, new WorkerStatusEventArgs(workerName, "stopped", null));
        WorkerCountChanged?.Invoke(this, EventArgs.Empty);
    }

    /// <summary>
    /// When true, StopAll cancels worker loops but does not kill child processes.
    /// Set before a graceful redeploy so in-flight processes can be handed off.
    /// </summary>
    public bool GracefulShutdown { get; set; }

    public void StopAll()
    {
        lock (_lock) StopAllInternal();
        WorkerCountChanged?.Invoke(this, EventArgs.Empty);
    }

    private void StopAllInternal()
    {
        if (GracefulShutdown)
            TaskRunner.SuppressKillOnCancel = true;
        foreach (var kv in _workers)
            kv.Value.Cts.Cancel();
        _workers.Clear();
        _workerStates.Clear();
    }

    public void Dispose()
    {
        lock (_lock)
        {
            _disposed = true;
            StopAllInternal();
        }
    }

    public IReadOnlyList<string> GetWorkerNames()
    {
        lock (_lock) return _workers.Keys.ToList();
    }

    /// <summary>
    /// Called on startup: loads handoff.json written by the previous instance,
    /// reattaches to any still-running child processes, monitors them to completion,
    /// and reports results to Redis.
    /// </summary>
    public void AdoptHandoff()
    {
        var handoff = ProcessHandoff.TryLoad();
        if (handoff == null || handoff.ActiveJobs.Count == 0) return;

        AppLog.Info($"[Handoff] Found {handoff.ActiveJobs.Count} job(s) from previous instance (written {handoff.WrittenAt})");

        var conn = _connection;
        if (conn == null || !conn.IsConnected)
        {
            AppLog.Warning("[Handoff] No Redis connection yet — cannot adopt jobs");
            return;
        }

        var db = conn.GetDatabase();
        var consumer = new RqJobConsumer(db, _queueName);

        foreach (var entry in handoff.ActiveJobs)
        {
            var capturedEntry = entry;
            Task.Run(() => MonitorAdoptedProcess(capturedEntry, consumer));
        }
    }

    private void MonitorAdoptedProcess(HandoffEntry entry, RqJobConsumer consumer)
    {
        var tag = $"[adopted][{entry.WorkerName}]";
        AppLog.Info($"{tag} Job {entry.JobId}: reattaching to PID={entry.ProcessId}");

        if (!IsProcessAlive(entry.ProcessId))
        {
            AppLog.Info($"{tag} Job {entry.JobId}: PID={entry.ProcessId} already gone - checking sidecar");
            FinalizeAdoptedJob(entry, consumer, tag, exitCode: null);
            return;
        }

        var adoptedAt = DateTime.UtcNow;
        AdoptedJobStarted?.Invoke(this, new AdoptedJobEventArgs(entry.JobId, entry.WorkerName, entry.ProcessId, adoptedAt) { ModelPath = entry.ModelPath });

        var startedAt = DateTime.TryParse(entry.StartedAt, out var sa) ? sa : DateTime.UtcNow;
        var deadline = startedAt.AddSeconds(entry.TimeoutSeconds);

        try
        {
            while (IsProcessAlive(entry.ProcessId))
            {
                if (DateTime.UtcNow > deadline)
                {
                    AppLog.Warning($"{tag} Job {entry.JobId}: timeout reached - killing PID={entry.ProcessId}");
                    try
                    {
                        using var p = System.Diagnostics.Process.GetProcessById(entry.ProcessId);
                        p.Kill(entireProcessTree: true);
                    }
                    catch { }
                    consumer.MarkFailed(entry.JobId, "Worker redeployed - adopted job timed out and was killed");
                    AdoptedJobFinished?.Invoke(this, new AdoptedJobEventArgs(entry.JobId, entry.WorkerName, entry.ProcessId, adoptedAt) { Success = false });
                    return;
                }

                try { consumer.Heartbeat(entry.JobId); } catch { }
                System.Threading.Thread.Sleep(15_000);
            }

            AppLog.Info($"{tag} Job {entry.JobId}: PID={entry.ProcessId} exited");
            FinalizeAdoptedJob(entry, consumer, tag, exitCode: null, adoptedAt);
        }
        catch (Exception ex)
        {
            AppLog.Warning($"{tag} Job {entry.JobId}: monitor error - {ex.Message}");
            consumer.MarkFailed(entry.JobId, $"Worker redeployed - monitor error: {ex.Message}");
            AdoptedJobFinished?.Invoke(this, new AdoptedJobEventArgs(entry.JobId, entry.WorkerName, entry.ProcessId, adoptedAt) { Success = false });
        }
    }

    /// <summary>
    /// Reliable process-alive check that doesn't require a pre-existing handle.
    /// GetProcessById + HasExited is unreliable on attached handles; re-opening
    /// the process fresh each time avoids "not started by this object" errors.
    /// </summary>
    private static bool IsProcessAlive(int pid)
    {
        try
        {
            using var p = System.Diagnostics.Process.GetProcessById(pid);
            return !p.HasExited;
        }
        catch
        {
            return false;
        }
    }

    private void FinalizeAdoptedJob(HandoffEntry entry, RqJobConsumer consumer, string tag, int? exitCode, DateTime? adoptedAt = null)
    {
        var sidecarResult = TryReadSidecar(entry.ModelPath, entry.JobId);
        bool success;

        if (sidecarResult != null)
        {
            var hasError = sidecarResult.TryGetValue("error", out var errVal) && errVal != null;
            var hasSuccess = sidecarResult.TryGetValue("success", out var s);
            success = hasSuccess ? s is true : !hasError;

            if (success)
            {
                consumer.MarkFinished(entry.JobId, sidecarResult);
                AppLog.Info($"{tag} Job {entry.JobId}: [FINISHED] (from sidecar)");
            }
            else
            {
                var err = hasError ? errVal?.ToString() : null;
                consumer.MarkFailed(entry.JobId, err ?? "Job failed (from sidecar)");
                AppLog.Warning($"{tag} Job {entry.JobId}: [FAILED] (from sidecar) {err}");
            }
        }
        else if (exitCode == 0)
        {
            success = true;
            var result = new Dictionary<string, object?>
            {
                ["success"] = true,
                ["exit_code"] = exitCode,
                ["note"] = "Result from adopted process (no sidecar found)",
            };
            consumer.MarkFinished(entry.JobId, result);
            AppLog.Info($"{tag} Job {entry.JobId}: [FINISHED] exit_code=0, no sidecar");
        }
        else
        {
            success = false;
            var msg = exitCode.HasValue
                ? $"Worker redeployed, process exited with code {exitCode}, no result sidecar found"
                : "Worker redeployed, process exited, no result sidecar found";
            consumer.MarkFailed(entry.JobId, msg);
            AppLog.Warning($"{tag} Job {entry.JobId}: [FAILED] {msg}");
        }

        AdoptedJobFinished?.Invoke(this, new AdoptedJobEventArgs(
            entry.JobId, entry.WorkerName, entry.ProcessId, adoptedAt ?? DateTime.UtcNow) { Success = success });
    }

    /// <summary>
    /// Searches for a sidecar result file written by the job script.
    /// Looks for *.rbp_rw_result.json and *.rw_result.json patterns near the model path.
    /// </summary>
    private static Dictionary<string, object?>? TryReadSidecar(string? modelPath, string jobId)
    {
        var candidates = new List<string>();

        if (!string.IsNullOrEmpty(modelPath))
        {
            var dir = Path.GetDirectoryName(modelPath) ?? "";
            var stem = Path.GetFileNameWithoutExtension(modelPath);
            // Sidecar next to the model (written by detach_repath_rbp.py)
            candidates.Add(Path.Combine(dir, stem + ".rbp_rw_result.json"));
            candidates.Add(Path.Combine(dir, stem + ".rw_result.json"));
            // Detached variant (_detached suffix)
            var detachedStem = stem.EndsWith("_detached") ? stem : stem + "_detached";
            candidates.Add(Path.Combine(dir, detachedStem + ".rbp_rw_result.json"));
        }

        // Also check next to the exe
        candidates.Add(Path.Combine(AppContext.BaseDirectory, jobId + ".rbp_rw_result.json"));
        candidates.Add(Path.Combine(AppContext.BaseDirectory, jobId + ".rw_result.json"));

        // Check %TEMP%\rbp_detach_repath where Run-RBPDetachRepath.ps1 writes the pre-flight sidecar
        if (!string.IsNullOrEmpty(modelPath))
        {
            var stem2 = Path.GetFileNameWithoutExtension(modelPath);
            var rbpTempDir = Path.Combine(Path.GetTempPath(), "rbp_detach_repath");
            candidates.Add(Path.Combine(rbpTempDir, stem2 + ".rbp_rw_result.json"));
        }

        foreach (var path in candidates)
        {
            AppLog.Info($"[Sidecar] checking: {path}");
            if (!File.Exists(path)) continue;
            try
            {
                var json = File.ReadAllText(path);
                AppLog.Info($"[Sidecar] found: {path} ({json.Length} bytes)");
                var doc = System.Text.Json.JsonDocument.Parse(json);
                var dict = new Dictionary<string, object?>();
                foreach (var prop in doc.RootElement.EnumerateObject())
                {
                    dict[prop.Name] = prop.Value.ValueKind switch
                    {
                        System.Text.Json.JsonValueKind.True  => (object?)true,
                        System.Text.Json.JsonValueKind.False => false,
                        System.Text.Json.JsonValueKind.Null  => null,
                        System.Text.Json.JsonValueKind.Number => prop.Value.TryGetInt64(out var i) ? i : prop.Value.GetDouble(),
                        _ => prop.Value.GetString(),
                    };
                }
                return dict;
            }
            catch (Exception ex)
            {
                AppLog.Warning($"[Sidecar] failed to read {path}: {ex.Message}");
            }
        }
        AppLog.Warning($"[Sidecar] no sidecar found for job {jobId} (checked {candidates.Count} paths)");
        return null;
    }

    private void CleanupDeadWorkers()
    {
        var dead = _workers.Where(kv => !kv.Value.Thread.IsAlive).Select(kv => kv.Key).ToList();
        foreach (var name in dead)
        {
            _workers[name].Cts.Dispose();
            _workers.Remove(name);
            _workerStates.Remove(name);
        }
    }

    private void WorkerLoop(string workerName, CancellationToken token)
    {
        var conn = _connection;
        if (conn == null || !conn.IsConnected) return;

        var db = conn.GetDatabase();
        var consumer = new RqJobConsumer(db, _queueName);

        try { consumer.RegisterWorker(workerName); } catch { }
        AppLog.Info($"[{workerName}] Worker thread started, queue={_queueName}");

        SetState(workerName, false, null);
        RaiseStatus(workerName, "idle", null);

        while (!token.IsCancellationRequested)
        {
            try
            {
                var jobId = consumer.DequeueJob(timeoutSeconds: 2);
                if (jobId == null)
                {
                    Thread.Sleep(500);
                    continue;
                }

                var jobData = consumer.ReadJobData(jobId);
                if (jobData == null)
                {
                    AppLog.Error($"[{workerName}] Job {jobId}: ReadJobData returned null, marking failed");
                    consumer.MarkFailed(jobId, "Could not unpickle job data");
                    continue;
                }

                var modelPath = jobData.TryGetValue("model_path", out var mp) ? mp?.ToString() : null;
                AppLog.Info($"[{workerName}] Job {jobId}: unpickled OK, command_type={jobData.GetValueOrDefault("command_type")}");

                // Start gate: stagger job starts so two workers never launch at the same moment
                TimeSpan waitTime;
                lock (_startGateLock)
                {
                    var now = DateTime.UtcNow;
                    if (now < _nextAllowedStart)
                        waitTime = _nextAllowedStart - now;
                    else
                        waitTime = TimeSpan.Zero;
                    var startAt = now > _nextAllowedStart ? now : _nextAllowedStart;
                    _nextAllowedStart = startAt + TimeSpan.FromSeconds(_jobStartDelaySeconds);
                }
                if (waitTime > TimeSpan.Zero)
                {
                    var remainingMs = (int)waitTime.TotalMilliseconds;
                    AppLog.Info($"[{workerName}] Job {jobId}: start-delay {remainingMs / 1000.0:F1}s (stagger)");
                    while (remainingMs > 0 && !token.IsCancellationRequested)
                    {
                        var sec = (remainingMs + 999) / 1000;
                        SetState(workerName, false, jobId, modelPath);
                        RaiseStatus(workerName, $"start-delay ({sec}s)", jobId, modelPath);
                        var sleepMs = Math.Min(1000, remainingMs);
                        Thread.Sleep(sleepMs);
                        remainingMs -= sleepMs;
                    }
                    if (token.IsCancellationRequested)
                    {
                        consumer.MarkFailed(jobId, "Worker stopped before job could start");
                        AppLog.Warning($"[{workerName}] Job {jobId}: [CANCELLED] stopped during start-delay");
                        continue;
                    }
                }

                consumer.MarkStarted(jobId, workerName);
                AppLog.Info($"[{workerName}] Job {jobId}: [STARTED]");
                consumer.SetWorkerState(workerName, "busy", jobId);
                SetState(workerName, true, jobId, modelPath);
                RaiseStatus(workerName, "busy", jobId, modelPath);
                JobAccepted?.Invoke(this, new JobAcceptedEventArgs(jobId));

                AppLog.Info($"[{workerName}] Job {jobId}: executing command...");
                jobData["job_id"] = jobId;
                var resultTask = Task.Run(() => TaskRunner.RunRevitCommand(jobData, token, workerName));
                while (!resultTask.IsCompleted)
                {
                    if (resultTask.Wait(TimeSpan.FromSeconds(15)))
                        break;
                    if (token.IsCancellationRequested)
                        break;
                    try { consumer.Heartbeat(jobId); consumer.SetWorkerState(workerName, "busy", jobId); }
                    catch { }
                }
                // RunRevitCommand is async; Task.Run(Func<Task<T>>) returns Task<T>, so one GetResult() gives the dictionary
                var result = resultTask.GetAwaiter().GetResult();

                var cancelled = result.TryGetValue("cancelled", out var cancelVal)
                    && cancelVal is bool cancelledBool
                    && cancelledBool;
                if (cancelled)
                {
                    var msg = result.TryGetValue("error", out var ce) ? ce?.ToString() ?? "Worker stopped during execution" : "Worker stopped during execution";
                    consumer.MarkFailed(jobId, msg);
                    AppLog.Warning($"[{workerName}] Job {jobId}: [CANCELLED] {msg}");
                    consumer.SetWorkerState(workerName, "idle");
                    SetState(workerName, false, null);
                    RaiseStatus(workerName, "idle", null);
                    continue;
                }

                var success = result.TryGetValue("success", out var s) && s is true;
                var exitCode = result.TryGetValue("exit_code", out var ec) ? ec : null;
                if (success)
                {
                    consumer.MarkFinished(jobId, result);
                    AppLog.Info($"[{workerName}] Job {jobId}: [FINISHED] exit_code={exitCode}");
                }
                else
                {
                    var errMsg = result.TryGetValue("error", out var err) ? err?.ToString() ?? "Unknown error" : "Unknown error";
                    consumer.MarkFailed(jobId, errMsg);
                    AppLog.Error($"[{workerName}] Job {jobId}: [FAILED] exit_code={exitCode} error={errMsg}");
                }

                consumer.SetWorkerState(workerName, "idle");
                SetState(workerName, false, null);
                RaiseStatus(workerName, "idle", null);
            }
            catch (ObjectDisposedException) { break; }
            catch (RedisConnectionException)
            {
                if (token.IsCancellationRequested) break;
                Thread.Sleep(3000);
            }
            catch (Exception ex)
            {
                if (token.IsCancellationRequested) break;
                AppLog.Error($"[{workerName}] Unhandled: {ex.Message}");
                WorkerError?.Invoke(this, new WorkerErrorEventArgs($"[{workerName}] {ex.Message}"));
                Thread.Sleep(2000);
            }
        }

        try { consumer.UnregisterWorker(workerName); } catch { }
        lock (_lock) _workerStates.Remove(workerName);
        RaiseStatus(workerName, "stopped", null);
    }

    private void SetState(string workerName, bool busy, string? jobId, string? modelPath = null)
    {
        lock (_lock)
        {
            _workerStates[workerName] = new WorkerState(workerName, busy, jobId, modelPath);
        }
    }

    private void RaiseStatus(string workerName, string state, string? jobId, string? modelPath = null)
    {
        WorkerStatusChanged?.Invoke(this, new WorkerStatusEventArgs(workerName, state, jobId, modelPath));
    }

    private record WorkerState(string Name, bool IsBusy, string? JobId, string? ModelPath = null);
}

public sealed class WorkerStatusEventArgs : EventArgs
{
    public string WorkerName { get; }
    public string State { get; }
    public string? CurrentJobId { get; }
    public string? ModelPath { get; }
    public WorkerStatusEventArgs(string workerName, string state, string? currentJobId, string? modelPath = null)
    {
        WorkerName = workerName;
        State = state;
        CurrentJobId = currentJobId;
        ModelPath = modelPath;
    }
}

public sealed class JobAcceptedEventArgs : EventArgs
{
    public string JobId { get; }
    public JobAcceptedEventArgs(string jobId) => JobId = jobId;
}

public sealed class WorkerErrorEventArgs : EventArgs
{
    public string Message { get; }
    public WorkerErrorEventArgs(string message) => Message = message;
}

public sealed class AdoptedJobEventArgs : EventArgs
{
    public string JobId { get; }
    public string OriginalWorkerName { get; }
    public int Pid { get; }
    public DateTime AdoptedAt { get; }
    public string? ModelPath { get; init; }
    public bool Success { get; init; }

    public AdoptedJobEventArgs(string jobId, string originalWorkerName, int pid, DateTime adoptedAt)
    {
        JobId = jobId;
        OriginalWorkerName = originalWorkerName;
        Pid = pid;
        AdoptedAt = adoptedAt;
    }
}
