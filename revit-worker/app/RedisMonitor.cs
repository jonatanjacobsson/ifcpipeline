using StackExchange.Redis;

namespace RevitWorkerApp;

/// <summary>
/// Manages the Redis connection with automatic reconnection.
/// </summary>
public sealed class RedisMonitor : IDisposable
{
    private ConnectionMultiplexer? _connection;
    private readonly object _connectionLock = new();
    private string? _lastUrl;
    private Thread? _watchdog;
    private volatile bool _disposed;
    private volatile bool _autoReconnect;

    private const int WatchdogIntervalMs = 10_000;
    private const int ReconnectBackoffMs = 5_000;
    private const int MaxReconnectBackoffMs = 60_000;

    public event EventHandler? ConnectionStateChanged;
    public event EventHandler? Reconnected;

    public bool IsConnected => _connection?.IsConnected ?? false;
    public ConnectionMultiplexer? Connection => _connection;

    public void Connect(string redisUrl)
    {
        if (string.IsNullOrWhiteSpace(redisUrl)) return;
        lock (_connectionLock)
        {
            DisconnectInternal();
            _lastUrl = redisUrl;
            ConnectInternal(redisUrl);
            _autoReconnect = true;
            EnsureWatchdog();
        }
    }

    public void Disconnect()
    {
        lock (_connectionLock)
        {
            _autoReconnect = false;
            DisconnectInternal();
            ConnectionStateChanged?.Invoke(this, EventArgs.Empty);
        }
    }

    public void EnableAutoReconnect(string redisUrl)
    {
        lock (_connectionLock)
        {
            _lastUrl = redisUrl;
            _autoReconnect = true;
            EnsureWatchdog();
        }
    }

    private void ConnectInternal(string redisUrl)
    {
        var opts = ParseRedisUrl(redisUrl);
        opts.AbortOnConnectFail = false;
        opts.ConnectTimeout = 4000;
        opts.SyncTimeout = 3000;
        opts.AsyncTimeout = 3000;
        opts.ReconnectRetryPolicy = new ExponentialRetry(ReconnectBackoffMs);
        AppLog.Info($"Connecting to Redis: {redisUrl}");
        _connection = ConnectionMultiplexer.Connect(opts);
        if (!_connection.IsConnected)
            throw new RedisConnectionException(ConnectionFailureType.UnableToConnect,
                "Could not reach Redis at " + redisUrl);
        AppLog.Info($"Redis connected OK: {_connection.GetEndPoints().FirstOrDefault()}");
        ConnectionStateChanged?.Invoke(this, EventArgs.Empty);
    }

    private void EnsureWatchdog()
    {
        if (_watchdog is { IsAlive: true }) return;
        _watchdog = new Thread(WatchdogLoop) { IsBackground = true, Name = "RedisWatchdog" };
        _watchdog.Start();
    }

    private void WatchdogLoop()
    {
        var backoff = ReconnectBackoffMs;

        while (!_disposed)
        {
            Thread.Sleep(WatchdogIntervalMs);
            if (_disposed || !_autoReconnect) break;

            var url = _lastUrl;
            if (string.IsNullOrEmpty(url)) continue;

            var connected = false;
            lock (_connectionLock)
            {
                connected = _connection?.IsConnected ?? false;
            }

            if (connected)
            {
                backoff = ReconnectBackoffMs;
                continue;
            }

            AppLog.Warning($"Redis connection lost — reconnecting in {backoff / 1000}s...");
            Thread.Sleep(backoff);
            if (_disposed || !_autoReconnect) break;

            try
            {
                lock (_connectionLock)
                {
                    DisconnectInternal();
                    ConnectInternal(url);
                }
                backoff = ReconnectBackoffMs;
                AppLog.Info("Redis reconnected successfully");
                Reconnected?.Invoke(this, EventArgs.Empty);
            }
            catch (Exception ex)
            {
                AppLog.Error($"Redis reconnect failed: {ex.Message}");
                ConnectionStateChanged?.Invoke(this, EventArgs.Empty);
                backoff = Math.Min(backoff * 2, MaxReconnectBackoffMs);
            }
        }
    }

    private void DisconnectInternal()
    {
        _connection?.Dispose();
        _connection = null;
    }

    public void Dispose()
    {
        _disposed = true;
        _autoReconnect = false;
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
