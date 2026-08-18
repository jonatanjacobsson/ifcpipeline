using Xunit;
using System.Collections.Generic;
using RevitWorkerApp;

namespace RevitWorkerApp.Tests;

public class ParseSentinelTests
{
    [Fact]
    public void ParseSentinel_WithValidSentinelLine_MergesDataIntoResult()
    {
        // Arrange
        var baseResult = new Dictionary<string, object?>
        {
            ["success"] = true,
            ["exit_code"] = 0,
            ["stdout"] = null,
            ["stderr"] = "some stderr",
            ["started_at"] = "2024-01-01T00:00:00Z",
            ["finished_at"] = "2024-01-01T00:01:00Z"
        };

        var stdout = @"Starting Revit...
Processing model...
RW_RESULT:{""output_path"":""C:\\output\\building.ifc"",""model_name"":""Building A""}
Done.";

        // Act
        var (result, cleanedStdout) = TaskRunner.ParseSentinel(baseResult, stdout);

        // Assert
        Assert.True((bool?)result["success"]);
        Assert.Equal(0, result["exit_code"]);
        Assert.Equal("some stderr", result["stderr"]);
        Assert.Equal("C:\\output\\building.ifc", result["output_path"]);
        Assert.Equal("Building A", result["model_name"]);

        // The sentinel line should be stripped from stdout
        Assert.DoesNotContain("RW_RESULT", cleanedStdout);
        Assert.Contains("Starting Revit...", cleanedStdout);
        Assert.Contains("Processing model...", cleanedStdout);
        Assert.Contains("Done.", cleanedStdout);
    }

    [Fact]
    public void ParseSentinel_WithMalformedJson_SkipsMergeAndContinues()
    {
        // Arrange
        var baseResult = new Dictionary<string, object?>
        {
            ["success"] = true,
            ["exit_code"] = 0
        };

        var stdout = @"Processing...
RW_RESULT:{invalid json here}
Done.";

        // Act
        var (result, cleanedStdout) = TaskRunner.ParseSentinel(baseResult, stdout);

        // Assert
        Assert.True((bool?)result["success"]);
        Assert.Equal(0, result["exit_code"]);
        // No merged data since JSON was invalid
        Assert.False(result.ContainsKey("output_path"));

        // Sentinel line still stripped
        Assert.DoesNotContain("RW_RESULT", cleanedStdout);
    }

    [Fact]
    public void ParseSentinel_WithNoSentinelLine_ReturnsUnchangedResult()
    {
        // Arrange
        var baseResult = new Dictionary<string, object?>
        {
            ["success"] = true,
            ["exit_code"] = 0,
            ["stdout"] = null
        };

        var stdout = "Regular output without sentinel";

        // Act
        var (result, cleanedStdout) = TaskRunner.ParseSentinel(baseResult, stdout);

        // Assert
        Assert.True((bool?)result["success"]);
        Assert.Equal(0, result["exit_code"]);
        Assert.Equal("Regular output without sentinel", cleanedStdout);
    }

    [Fact]
    public void ParseSentinel_WithSentinelDataOverridingBaseResult_PrefersSentinelData()
    {
        // Arrange
        var baseResult = new Dictionary<string, object?>
        {
            ["success"] = false,
            ["custom_field"] = "base_value"
        };

        var stdout = @"RW_RESULT:{""success"":true,""custom_field"":""sentinel_value"",""new_field"":""new_value""}";

        // Act
        var (result, cleanedStdout) = TaskRunner.ParseSentinel(baseResult, stdout);

        // Assert
        Assert.True((bool?)result["success"]); // Overridden by sentinel
        Assert.Equal("sentinel_value", result["custom_field"]); // Overridden by sentinel
        Assert.Equal("new_value", result["new_field"]); // New field added
    }

    [Fact]
    public void ParseSentinel_WithJsonArraysAndObjects_PreservesStructure()
    {
        var baseResult = new Dictionary<string, object?> { ["success"] = true };
        var stdout = @"RW_RESULT:{""warnings_total"":0,""families_names"":[""A"",""Project Base Point""],""coord_family_instances"":{""project_base_point"":[],""survey_point"":[]}}";

        var (result, _) = TaskRunner.ParseSentinel(baseResult, stdout);

        Assert.Equal(0, result["warnings_total"]);
        Assert.True(result["families_names"] is List<object?>);
        var names = (List<object?>)result["families_names"]!;
        Assert.Equal(2, names.Count);
        Assert.Equal("Project Base Point", names[1]?.ToString());
        Assert.True(result["coord_family_instances"] is Dictionary<string, object?>);
    }
}