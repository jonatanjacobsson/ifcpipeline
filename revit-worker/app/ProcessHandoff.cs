using System.Text.Json;

namespace RevitWorkerApp;

/// <summary>
/// Persists the set of active child processes to disk so a redeployed instance
/// can reattach and continue monitoring them.
/// </summary>
public sealed class ProcessHandoff
{
    public List<HandoffEntry> ActiveJobs { get; set; } = new();
    public string WrittenAt { get; set; } = "";

    public static string FilePath =>
        Path.Combine(AppContext.BaseDirectory, "handoff.json");

    public void Save()
    {
        WrittenAt = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ");
        var json = JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true });
        var tmp = FilePath + ".tmp";
        File.WriteAllText(tmp, json);
        File.Move(tmp, FilePath, overwrite: true);
    }

    /// <summary>
    /// Load and atomically delete the handoff file.
    /// Returns null if the file does not exist or cannot be parsed.
    /// </summary>
    public static ProcessHandoff? TryLoad()
    {
        var path = FilePath;
        if (!File.Exists(path)) return null;
        try
        {
            var json = File.ReadAllText(path);
            File.Delete(path);
            return JsonSerializer.Deserialize<ProcessHandoff>(json);
        }
        catch
        {
            try { File.Delete(path); } catch { }
            return null;
        }
    }
}

public sealed class HandoffEntry
{
    public string JobId { get; set; } = "";
    public int ProcessId { get; set; }
    public string WorkerName { get; set; } = "";
    public string CommandType { get; set; } = "";
    public string? ModelPath { get; set; }
    public int TimeoutSeconds { get; set; }
    public string StartedAt { get; set; } = "";
}
