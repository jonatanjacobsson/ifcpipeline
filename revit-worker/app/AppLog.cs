namespace RevitWorkerApp;

/// <summary>
/// Logger that writes to a file next to the exe AND to an in-app GUI panel.
/// </summary>
public static class AppLog
{
    private static string? _logPath;
    private static readonly object Lock = new();
    private static readonly List<string> Buffer = new();
    public static event Action<string>? LogAdded;

    static AppLog()
    {
        _logPath = FindWritablePath();
    }

    public static string LogFilePath => _logPath ?? "(no writable path found)";

    public static IReadOnlyList<string> GetBuffer()
    {
        lock (Lock) return Buffer.ToList();
    }

    public static void Info(string message) => Write("INFO", message);
    public static void Error(string message) => Write("ERROR", message);
    public static void Warning(string message) => Write("WARNING", message);

    public static void Debug(string tag, string message, object? data = null)
    {
        var dataStr = data != null ? $" | data={data}" : "";
        Write("DEBUG", $"[{tag}] {message}{dataStr}");
    }

    private static void Write(string level, string message)
    {
        var line = $"{DateTime.UtcNow:HH:mm:ss.fff} [{level}] {message}";
        lock (Lock)
        {
            Buffer.Add(line);
            if (Buffer.Count > 2000)
                Buffer.RemoveAt(0);
        }
        LogAdded?.Invoke(line);
        TryWriteFile(line);
    }

    private static void TryWriteFile(string line)
    {
        if (_logPath == null) return;
        try
        {
            lock (Lock)
            {
                File.AppendAllText(_logPath, line + Environment.NewLine);
            }
        }
        catch { }
    }

    private static string? FindWritablePath()
    {
        var candidates = new List<string>();

        var procPath = Environment.ProcessPath;
        if (procPath != null)
            candidates.Add(Path.Combine(Path.GetDirectoryName(procPath)!, "revit-worker.log"));

        candidates.Add(Path.Combine(AppContext.BaseDirectory, "revit-worker.log"));
        candidates.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RevitWorkerApp", "revit-worker.log"));

        foreach (var path in candidates)
        {
            try
            {
                var dir = Path.GetDirectoryName(path)!;
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                File.AppendAllText(path, $"--- Log started {DateTime.UtcNow:yyyy-MM-dd HH:mm:ss} UTC ---{Environment.NewLine}");
                return path;
            }
            catch { }
        }
        return null;
    }
}
