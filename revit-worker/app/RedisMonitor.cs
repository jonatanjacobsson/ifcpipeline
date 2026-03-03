using StackExchange.Redis;

namespace RevitWorkerApp;

/// <summary>
/// Manages the Redis connection. Worker status is now tracked directly by
/// WorkerProcessManager (no external PID monitoring needed).
/// </summary>
public sealed class RedisMonitor : IDisposable
{
    private ConnectionMultiplexer? _connection;
    private readonly object _connectionLock = new();

    public event EventHandler? ConnectionStateChanged;

    public bool IsConnected => _connection?.IsConnected ?? false;
    public ConnectionMultiplexer? Connection => _connection;

    public void Connect(string redisUrl)
    {
        if (string.IsNullOrWhiteSpace(redisUrl)) return;
        lock (_connectionLock)
        {
            DisconnectInternal();
            try
            {
                var opts = ParseRedisUrl(redisUrl);
                opts.AbortOnConnectFail = false;
                opts.ConnectTimeout = 5000;
                AppLog.Info($"Connecting to Redis: {redisUrl}");
                _connection = ConnectionMultiplexer.Connect(opts);
                if (!_connection.IsConnected)
                    throw new RedisConnectionException(ConnectionFailureType.UnableToConnect,
                        "Could not reach Redis at " + redisUrl);
                AppLog.Info($"Redis connected OK: {_connection.GetEndPoints().FirstOrDefault()}");
                ConnectionStateChanged?.Invoke(this, EventArgs.Empty);
            }
            catch (Exception ex)
            {
                AppLog.Error($"Redis connect failed: {ex.Message}");
                ConnectionStateChanged?.Invoke(this, EventArgs.Empty);
                throw;
            }
        }
    }

    public void Disconnect()
    {
        lock (_connectionLock)
        {
            DisconnectInternal();
            ConnectionStateChanged?.Invoke(this, EventArgs.Empty);
        }
    }

    private void DisconnectInternal()
    {
        _connection?.Dispose();
        _connection = null;
    }

    public void Dispose()
    {
        lock (_connectionLock)
        {
            DisconnectInternal();
        }
    }

    private static ConfigurationOptions ParseRedisUrl(string url)
    {
        url = url.Trim();
        if (url.StartsWith("redis://", StringComparison.OrdinalIgnoreCase))
        {
            var uri = new Uri(url);
            var host = uri.Host;
            var port = uri.Port > 0 ? uri.Port : 6379;
            var db = uri.AbsolutePath.TrimStart('/');
            var opts = new ConfigurationOptions { EndPoints = { { host, port } } };
            if (int.TryParse(db, out var dbNum))
                opts.DefaultDatabase = dbNum;
            if (!string.IsNullOrEmpty(uri.UserInfo))
            {
                var parts = uri.UserInfo.Split(':', 2);
                if (parts.Length == 2)
                    opts.Password = Uri.UnescapeDataString(parts[1]);
                else if (parts.Length == 1 && !string.IsNullOrEmpty(parts[0]))
                    opts.Password = Uri.UnescapeDataString(parts[0]);
            }
            return opts;
        }
        return ConfigurationOptions.Parse(url);
    }
}
