using Razorvine.Pickle;
using StackExchange.Redis;
using System.Collections;
using System.IO.Compression;

namespace RevitWorkerApp;

/// <summary>
/// RQ-compatible job consumer. Reads jobs from an RQ queue via LPOP (FIFO),
/// unpickles the job data, and pickles results back -- full compatibility
/// with the Python RQ ecosystem without needing Python installed.
/// </summary>
public sealed class RqJobConsumer
{
    private readonly IDatabase _db;
    private readonly string _queueName;

    public RqJobConsumer(IDatabase db, string queueName)
    {
        _db = db;
        _queueName = queueName;
    }

    /// <summary>
    /// Pop from the RQ queue (FIFO). Returns the job ID or null if empty.
    /// RQ enqueues with RPUSH, so LPOP gives first-in-first-out ordering.
    /// </summary>
    public string? DequeueJob(int timeoutSeconds = 5)
    {
        var key = $"rq:queue:{_queueName}";
        var result = _db.ListLeftPop(key);
        return result.HasValue ? result.ToString() : null;
    }

    /// <summary>
    /// Read and unpickle the job payload from the rq:job hash.
    /// Returns the job_data dict as a Dictionary, or null if unreadable.
    /// </summary>
    public Dictionary<string, object?>? ReadJobData(string jobId)
    {
        var key = $"rq:job:{jobId}";
        var rawData = _db.HashGet(key, "data");
        if (!rawData.HasValue) return null;

        try
        {
            byte[] pickleBytes = (byte[])rawData!;

            // RQ >= 1.16 compresses job data with zlib (header byte 0x78)
            if (pickleBytes.Length > 2 && pickleBytes[0] == 0x78)
                pickleBytes = ZlibDecompress(pickleBytes);

            var unpickler = new Unpickler();
            var obj = unpickler.loads(pickleBytes);

            // RQ pickles: (func_name, instance, args_tuple, kwargs_dict)
            if (obj is object[] tuple && tuple.Length >= 3)
            {
                var args = tuple[2] as object[];
                if (args is { Length: > 0 })
                    return HashtableToDict(args[0]);
            }

            AppLog.Error($"Job {jobId}: unexpected pickle structure, returning null");
            return null;
        }
        catch (Exception ex)
        {
            AppLog.Error($"Job {jobId}: failed to read job data — {ex.GetType().Name}: {ex.Message}");
            return null;
        }
    }

    private static string UtcNow() => DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ");
    private static double UnixTimestamp() => DateTimeOffset.UtcNow.ToUnixTimeSeconds();

    /// <summary>
    /// Mark job as started -- mirrors Python RQ's prepare_for_execution().
    /// Sets status, worker_name, last_heartbeat, started_at on the job hash,
    /// and adds the job to the StartedJobRegistry sorted set.
    /// </summary>
    public void MarkStarted(string jobId, string workerName)
    {
        var key = $"rq:job:{jobId}";
        var now = UtcNow();
        _db.HashSet(key, [
            new HashEntry("status", "started"),
            new HashEntry("started_at", now),
            new HashEntry("worker_name", workerName),
            new HashEntry("last_heartbeat", now),
        ]);
        // Add to StartedJobRegistry / WIP (sorted set key = rq:wip:<queue>, score = timestamp + timeout)
        var timeout = (double)(_db.HashGet(key, "timeout").TryParse(out long t) ? t : 3600);
        _db.SortedSetAdd($"rq:wip:{_queueName}", jobId, UnixTimestamp() + timeout);
    }

    /// <summary>
    /// Send a heartbeat for a running job (keeps RQ from considering it abandoned).
    /// </summary>
    public void Heartbeat(string jobId)
    {
        var key = $"rq:job:{jobId}";
        _db.HashSet(key, "last_heartbeat", UtcNow());
    }

    /// <summary>
    /// Mark job as finished -- mirrors Python RQ's _handle_success().
    /// Updates the job hash, moves from StartedJobRegistry to FinishedJobRegistry.
    /// </summary>
    public void MarkFinished(string jobId, Dictionary<string, object?> result, int resultTtlSeconds = 86400)
    {
        var key = $"rq:job:{jobId}";
        var now = UtcNow();
        var pickledResult = PickleResult(result);

        _db.HashSet(key, [
            new HashEntry("status", "finished"),
            new HashEntry("ended_at", now),
            new HashEntry("result", pickledResult),
        ]);
        _db.KeyExpire(key, TimeSpan.FromSeconds(resultTtlSeconds));

        // Move from wip -> finished registry
        _db.SortedSetRemove($"rq:wip:{_queueName}", jobId);
        _db.SortedSetAdd($"rq:finished:{_queueName}", jobId, UnixTimestamp() + resultTtlSeconds);
    }

    /// <summary>
    /// Mark job as failed -- mirrors Python RQ's _handle_failure().
    /// Updates the job hash, moves from StartedJobRegistry to FailedJobRegistry.
    /// </summary>
    public void MarkFailed(string jobId, string errorMessage, int resultTtlSeconds = 86400)
    {
        var key = $"rq:job:{jobId}";
        var now = UtcNow();

        _db.HashSet(key, [
            new HashEntry("status", "failed"),
            new HashEntry("ended_at", now),
            new HashEntry("exc_info", errorMessage),
        ]);
        _db.KeyExpire(key, TimeSpan.FromSeconds(resultTtlSeconds));

        // Move from wip -> failed registry
        _db.SortedSetRemove($"rq:wip:{_queueName}", jobId);
        _db.SortedSetAdd($"rq:failed:{_queueName}", jobId, UnixTimestamp() + resultTtlSeconds);
    }

    /// <summary>
    /// Register this worker in Redis -- mirrors Python RQ's Worker.register_birth().
    /// Uses the global rq:workers set (not queue-specific).
    /// </summary>
    public void RegisterWorker(string workerName)
    {
        _db.SetAdd("rq:workers", workerName);
        var now = UtcNow();
        _db.HashSet($"rq:worker:{workerName}", [
            new HashEntry("birth", now),
            new HashEntry("last_heartbeat", now),
            new HashEntry("state", "idle"),
            new HashEntry("queues", _queueName),
            new HashEntry("current_job", ""),
        ]);
    }

    /// <summary>
    /// Update worker state and heartbeat in Redis.
    /// </summary>
    public void SetWorkerState(string workerName, string state, string currentJobId = "")
    {
        _db.HashSet($"rq:worker:{workerName}", [
            new HashEntry("state", state),
            new HashEntry("current_job", currentJobId),
            new HashEntry("last_heartbeat", UtcNow()),
        ]);
    }

    /// <summary>
    /// Unregister worker from Redis.
    /// </summary>
    public void UnregisterWorker(string workerName)
    {
        _db.SetRemove("rq:workers", workerName);
        _db.KeyDelete($"rq:worker:{workerName}");
    }

    private static byte[] PickleResult(Dictionary<string, object?> result)
    {
        var ht = new Hashtable();
        foreach (var kv in result)
            ht[kv.Key] = NormalizeForPickle(kv.Value);

        var pickler = new Pickler();
        return pickler.dumps(ht);
        // RQ stores result as raw pickle bytes (no zlib). Job data is zlib-compressed; result is not.
    }

    /// <summary>
    /// Converts .NET collection types to Hashtable/ArrayList so Razorvine
    /// pickles them as Python dict/list instead of opaque .NET types.
    /// </summary>
    private static object? NormalizeForPickle(object? value)
    {
        if (value == null) return null;

        if (value is Dictionary<string, object?> dict)
        {
            var ht = new Hashtable();
            foreach (var kv in dict)
                ht[kv.Key] = NormalizeForPickle(kv.Value);
            return ht;
        }

        if (value is System.Collections.IList list)
        {
            var al = new ArrayList(list.Count);
            foreach (var item in list)
                al.Add(NormalizeForPickle(item));
            return al;
        }

        return value;
    }

    private static byte[] ZlibDecompress(byte[] data)
    {
        using var input = new MemoryStream(data);
        using var zlib = new ZLibStream(input, CompressionMode.Decompress);
        using var output = new MemoryStream();
        zlib.CopyTo(output);
        return output.ToArray();
    }

    private static byte[] ZlibCompress(byte[] data)
    {
        using var output = new MemoryStream();
        using (var zlib = new ZLibStream(output, CompressionLevel.Fastest))
        {
            zlib.Write(data, 0, data.Length);
        }
        return output.ToArray();
    }

    private static Dictionary<string, object?>? HashtableToDict(object? obj)
    {
        if (obj is Hashtable ht)
        {
            var dict = new Dictionary<string, object?>();
            foreach (DictionaryEntry entry in ht)
                dict[entry.Key?.ToString() ?? ""] = entry.Value;
            return dict;
        }
        if (obj is IDictionary<string, object> d)
        {
            return d.ToDictionary(kv => kv.Key, kv => (object?)kv.Value);
        }
        return null;
    }
}
