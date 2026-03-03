using System.Windows.Forms;

namespace RevitWorkerApp;

static class Program
{
    private const string MutexName = "RevitWorkerApp.SingleInstance";

    private static bool IsRuntimeAvailable()
    {
        try
        {
            _ = typeof(System.Windows.Forms.Form).Assembly;
            return true;
        }
        catch
        {
            return false;
        }
    }

    [STAThread]
    static void Main()
    {
        ApplicationConfiguration.Initialize();

        Application.ThreadException += (_, e) =>
        {
            MessageBox.Show(e.Exception.ToString(), "Revit Worker - Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
        };
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
        {
            var ex = (Exception)e.ExceptionObject;
            MessageBox.Show(ex.ToString(), "Revit Worker - Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
        };

        try
        {
            if (!IsRuntimeAvailable())
            {
                var result = MessageBox.Show(
                    ".NET 8 Desktop Runtime is required but not installed.\n\n" +
                    "Click OK to open the download page, then install the\n" +
                    "\"x64\" version under \".NET Desktop Runtime 8.x\".",
                    "Revit Worker - Runtime Missing",
                    MessageBoxButtons.OKCancel, MessageBoxIcon.Warning);
                if (result == DialogResult.OK)
                {
                    System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                    {
                        FileName = "https://dotnet.microsoft.com/en-us/download/dotnet/8.0",
                        UseShellExecute = true
                    });
                }
                return;
            }

            using var mutex = new Mutex(true, MutexName, out var createdNew);
            if (!createdNew)
            {
                MessageBox.Show("Revit Worker is already running. Check the system tray (click the ^ arrow if you don't see the icon).", "Revit Worker",
                    MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            AppLog.Info($"Application starting. Base: {AppContext.BaseDirectory} Log: {AppLog.LogFilePath}");

            var context = new TrayApplicationContext();
            context.RunWithSettingsShown();
            Application.Run(context);
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.ToString(), "Revit Worker - Startup Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
