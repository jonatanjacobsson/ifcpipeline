using Xunit;
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using RevitWorkerApp;

namespace RevitWorkerApp.Tests;

public class UploadLogsTests
{
    [Fact]
    public async Task UploadLogs_WithNoApiGatewayUrl_SkipsUpload()
    {
        // Arrange
        var logFiles = new Dictionary<string, string?>
        {
            ["journal"] = "/fake/path/journal.txt",
            ["worker"] = "/fake/path/worker.log"
        };

        // Act
        var result = await TaskRunner.UploadLogs("job123", null, "api_key", logFiles);

        // Assert
        Assert.Empty(result);
    }

    [Fact]
    public async Task UploadLogs_WithNoApiKey_SkipsUpload()
    {
        // Arrange
        var logFiles = new Dictionary<string, string?>
        {
            ["journal"] = "/fake/path/journal.txt",
            ["worker"] = "/fake/path/worker.log"
        };

        // Act
        var result = await TaskRunner.UploadLogs("job123", "http://localhost:8000", null, logFiles);

        // Assert
        Assert.Empty(result);
    }

    [Fact]
    public async Task UploadLogs_WithNonExistentFiles_SkipsUpload()
    {
        // Arrange
        var logFiles = new Dictionary<string, string?>
        {
            ["journal"] = "/nonexistent/path/journal.txt",
            ["worker"] = "/nonexistent/path/worker.log"
        };

        // Act
        var result = await TaskRunner.UploadLogs("job123", "http://localhost:8000", "api_key", logFiles);

        // Assert
        Assert.Empty(result);
    }

    [Fact]
    public async Task UploadLogs_WithNullFilePaths_SkipsUpload()
    {
        // Arrange
        var logFiles = new Dictionary<string, string?>
        {
            ["journal"] = null,
            ["worker"] = null
        };

        // Act
        var result = await TaskRunner.UploadLogs("job123", "http://localhost:8000", "api_key", logFiles);

        // Assert
        Assert.Empty(result);
    }

    // Note: Testing actual HTTP uploads would require setting up a test server
    // or extensive mocking. For now, we test the early-return conditions.
    // In a real implementation, you'd want to mock HttpClient and verify
    // the correct multipart form data is sent.
}