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

    public event EventHandler<WorkerStatusEventArgs>? WorkerStatusChanged;
    public event EventHandler<JobAcceptedEventArgs>? JobAccepted;
    public event EventHandler<WorkerErrorEventArgs>? WorkerError;
    public event EventHandler? WorkerCountChanged;

    public int CurrentCount
    {
        get { lock (_lock) return _workers.Count(kv => kv.Value.Thread.IsAlive); }
    }

    public bool AnyBusy
    {
        get { lock (_lock) return _workerStates.Values.Any(s => s.IsBusy); }
    }

    public void Configure(string redisUrl, string queueNames)
    {
        _queueName = string.IsNullOrWhiteSpace(queueNames)
            ? AppSettings.DefaultQueueNames
            : queueNames.Trim().Split(',')[0].Trim();
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

    public void StopAll()
    {
        lock (_lock) StopAllInternal();
        WorkerCountChanged?.Invoke(this, EventArgs.Empty);
    }

    private void StopAllInternal()
    {
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
        // #region agent log
        AppLog.Info($"[{workerName}] Worker thread started, queue={_queueName}");
        // #endregion

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
                    // #region agent log H1
                    AppLog.Error($"[{workerName}] Job {jobId}: ReadJobData returned null, marking failed");
                    // #endregion
                    consumer.MarkFailed(jobId, "Could not unpickle job data");
                    continue;
                }

                AppLog.Info($"[{workerName}] Job {jobId}: unpickled OK, command_type={jobData.GetValueOrDefault("command_type")}");
                consumer.MarkStarted(jobId, workerName);
                AppLog.Info($"[{workerName}] Job {jobId}: [STARTED]");
                consumer.SetWorkerState(workerName, "busy", jobId);
                SetState(workerName, true, jobId);
                RaiseStatus(workerName, "busy", jobId);
                JobAccepted?.Invoke(this, new JobAcceptedEventArgs(jobId));

                AppLog.Info($"[{workerName}] Job {jobId}: executing command...");
                jobData["job_id"] = jobId;
                var resultTask = Task.Run(() => TaskRunner.RunRevitCommand(jobData));
                while (!resultTask.IsCompleted)
                {
                    if (resultTask.Wait(TimeSpan.FromSeconds(15)))
                        break;
                    try { consumer.Heartbeat(jobId); consumer.SetWorkerState(workerName, "busy", jobId); }
                    catch { }
                }
                // RunRevitCommand is async; Task.Run(Func<Task<T>>) returns Task<T>, so one GetResult() gives the dictionary
                var result = resultTask.GetAwaiter().GetResult();

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
                WorkerError?.Invoke(this, new WorkerErrorEventArgs($"[{workerName}] {ex.Message}"));
                Thread.Sleep(2000);
            }
        }

        try { consumer.UnregisterWorker(workerName); } catch { }
        lock (_lock) _workerStates.Remove(workerName);
        RaiseStatus(workerName, "stopped", null);
    }

    private void SetState(string workerName, bool busy, string? jobId)
    {
        lock (_lock)
        {
            _workerStates[workerName] = new WorkerState(workerName, busy, jobId);
        }
    }

    private void RaiseStatus(string workerName, string state, string? jobId)
    {
        WorkerStatusChanged?.Invoke(this, new WorkerStatusEventArgs(workerName, state, jobId));
    }

    private record WorkerState(string Name, bool IsBusy, string? JobId);
}

public sealed class WorkerStatusEventArgs : EventArgs
{
    public string WorkerName { get; }
    public string State { get; }
    public string? CurrentJobId { get; }
    public WorkerStatusEventArgs(string workerName, string state, string? currentJobId)
    {
        WorkerName = workerName;
        State = state;
        CurrentJobId = currentJobId;
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
