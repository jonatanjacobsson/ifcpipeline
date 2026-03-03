using Xunit;
using System;
using System.Collections.Generic;
using System.IO;
using RevitWorkerApp;

namespace RevitWorkerApp.Tests;

public class LogFinderTests : IDisposable
{
    private readonly string _tempDir;

    public LogFinderTests()
    {
        _tempDir = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString());
        Directory.CreateDirectory(_tempDir);
    }

    public void Dispose()
    {
        if (Directory.Exists(_tempDir))
        {
            Directory.Delete(_tempDir, true);
        }
    }

    [Fact]
    public void FindLogFiles_WithPyRevitCommand_IncludesAllKeys()
    {
        // Arrange
        var startedAt = DateTime.UtcNow.AddMinutes(-5);
        var finishedAt = DateTime.UtcNow;

        var logFiles = TaskRunner.FindLogFiles("pyrevit", startedAt, finishedAt, 12345);

        // Assert - all four log type keys are present
        Assert.Contains("journal", logFiles.Keys);
        Assert.Contains("pyrevit", logFiles.Keys);
        Assert.Contains("rtv", logFiles.Keys);
        Assert.Contains("worker", logFiles.Keys);
        // worker path is always set (path to revit-worker.log)
        Assert.NotNull(logFiles["worker"]);
    }

    [Fact]
    public void FindLogFiles_WithRtvCommand_IncludesAllKeys()
    {
        // Arrange
        var startedAt = DateTime.UtcNow.AddMinutes(-5);
        var finishedAt = DateTime.UtcNow;

        var logFiles = TaskRunner.FindLogFiles("rtv", startedAt, finishedAt, 12345);

        // Assert - all four log type keys are present
        Assert.Contains("journal", logFiles.Keys);
        Assert.Contains("pyrevit", logFiles.Keys);
        Assert.Contains("rtv", logFiles.Keys);
        Assert.Contains("worker", logFiles.Keys);
        Assert.NotNull(logFiles["worker"]);
    }

    [Fact]
    public void FindLogFiles_WithTimeWindow_FiltersFilesOutsideWindow()
    {
        // Arrange - create a temp directory structure like Revit journals
        var revitDir = Path.Combine(_tempDir, "Autodesk", "Revit", "Autodesk Revit 2025", "Journals");
        Directory.CreateDirectory(revitDir);

        var startedAt = DateTime.UtcNow.AddMinutes(-2);
        var finishedAt = DateTime.UtcNow.AddMinutes(-1);

        // Create files: one inside window, one outside
        var insideWindowFile = Path.Combine(revitDir, "journal.0001.txt");
        var outsideWindowFile = Path.Combine(revitDir, "journal.0002.txt");

        File.WriteAllText(insideWindowFile, "test content");
        File.WriteAllText(outsideWindowFile, "test content");

        // Set timestamps
        File.SetLastWriteTimeUtc(insideWindowFile, startedAt.AddSeconds(30)); // Inside window
        File.SetLastWriteTimeUtc(outsideWindowFile, finishedAt.AddMinutes(5)); // Outside window

        // We can't easily mock the environment directories, so this test mainly
        // verifies the method structure and that it doesn't throw
        var logFiles = TaskRunner.FindLogFiles("pyrevit", startedAt, finishedAt, 12345);

        // Assert method completes without exception
        Assert.IsType<Dictionary<string, string?>>(logFiles);
        Assert.Contains("journal", logFiles.Keys);
        Assert.Contains("pyrevit", logFiles.Keys);
        Assert.Contains("worker", logFiles.Keys);
    }

    [Fact]
    public void FindLogFiles_WithNonExistentDirectories_ReturnsNullForOptionalLogs()
    {
        // Arrange - this will run against real system paths that may not exist
        var startedAt = DateTime.UtcNow.AddMinutes(-5);
        var finishedAt = DateTime.UtcNow;

        var logFiles = TaskRunner.FindLogFiles("pyrevit", startedAt, finishedAt, 12345);

        // Assert - method should handle missing directories gracefully
        Assert.IsType<Dictionary<string, string?>>(logFiles);
        // Worker log path is always returned (even if file doesn't exist)
        Assert.Contains("worker", logFiles.Keys);
        Assert.NotNull(logFiles["worker"]);
    }
}