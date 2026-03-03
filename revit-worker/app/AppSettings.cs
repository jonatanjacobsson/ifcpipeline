using System.Text.Json;
using System.Text.Json.Serialization;

namespace RevitWorkerApp;

public sealed class AppSettings
{
    public const string DefaultQueueNames = "revit";
    public const int DefaultWorkerCount = 1;
    public const int MinWorkerCount = 1;
    public const int MaxWorkerCount = 8;

    public const string DefaultRedisUrl = "redis://bim-wsp1-ubnt:6379/0";

    [JsonPropertyName("redis_url")]
    public string RedisUrl { get; set; } = DefaultRedisUrl;

    [JsonPropertyName("worker_count")]
    public int WorkerCount { get; set; } = DefaultWorkerCount;

    [JsonPropertyName("queue_names")]
    public string QueueNames { get; set; } = DefaultQueueNames;

    [JsonPropertyName("api_gateway_url")]
    public string? ApiGatewayUrl { get; set; }

    [JsonPropertyName("api_key")]
    public string? ApiKey { get; set; }

    private static string GetSettingsPath()
    {
        var exeDir = AppContext.BaseDirectory;
        return Path.Combine(exeDir, "settings.json");
    }

    public static AppSettings Load()
    {
        var path = GetSettingsPath();
        if (!File.Exists(path))
            return new AppSettings();

        try
        {
            var json = File.ReadAllText(path);
            var settings = JsonSerializer.Deserialize<AppSettings>(json);
            return settings ?? new AppSettings();
        }
        catch
        {
            return new AppSettings();
        }
    }

    public void Save()
    {
        var path = GetSettingsPath();
        var json = JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllText(path, json);
    }

    public int ClampedWorkerCount => Math.Clamp(WorkerCount, MinWorkerCount, MaxWorkerCount);
}
