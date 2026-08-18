using System.Windows.Forms;

namespace RevitWorkerApp;

public sealed class TrayApplicationContext : ApplicationContext
{
    private readonly NotifyIcon _notifyIcon;
    private readonly WorkerProcessManager _processManager;
    private readonly RedisMonitor _redisMonitor;
    private readonly SynchronizationContext? _uiContext;
    private MainForm? _mainForm;
    private bool _paused;
    private bool _anyWorkerBusy;
    private Icon? _iconRed;
    private Icon? _iconGreen;
    private Icon? _iconOrange;

    public TrayApplicationContext()
    {
        _iconRed = IconGenerator.CreateRed();
        _iconGreen = IconGenerator.CreateGreen();
        _iconOrange = IconGenerator.CreateOrange();

        _processManager = new WorkerProcessManager();
        _redisMonitor = new RedisMonitor();

        _notifyIcon = new NotifyIcon
        {
            Icon = _iconRed,
            Text = $"Revit Worker v{Program.Version} (disconnected)",
            Visible = true
        };
        _uiContext = SynchronizationContext.Current;

        _notifyIcon.DoubleClick += (_, _) =>
        {
            EnsureMainForm();
            _mainForm!.Show();
            _mainForm.BringToFront();
        };

        var menu = new ContextMenuStrip();
        var openItem = new ToolStripMenuItem("Open Settings");
        openItem.Click += (_, _) =>
        {
            EnsureMainForm();
            _mainForm!.Show();
            _mainForm.BringToFront();
        };
        menu.Items.Add(openItem);

        var pauseItem = new ToolStripMenuItem("Pause");
        pauseItem.Click += (_, _) => TogglePause(pauseItem);
        menu.Items.Add(pauseItem);

        menu.Items.Add(new ToolStripSeparator());
        var quitItem = new ToolStripMenuItem("Quit");
        quitItem.Click += (_, _) => Exit();
        menu.Items.Add(quitItem);

        _notifyIcon.ContextMenuStrip = menu;

        _processManager.WorkerStatusChanged += OnWorkerStatusChanged;
        _processManager.JobAccepted += OnJobAccepted;
        _processManager.AdoptedJobStarted += (_, e) => PostToUI(() => _mainForm?.OnAdoptedJobStarted(e));
        _processManager.AdoptedJobFinished += (_, e) => PostToUI(() => _mainForm?.OnAdoptedJobFinished(e));
        _redisMonitor.ConnectionStateChanged += (_, _) => PostToUI(UpdateIconAndTooltip);
        _redisMonitor.Reconnected += (_, _) => PostToUI(OnReconnected);

        Application.ApplicationExit += (_, _) =>
        {
            _processManager.StopAll();
            _processManager.Dispose();
            _redisMonitor.Dispose();
            _notifyIcon.Visible = false;
            _iconRed?.Dispose();
            _iconGreen?.Dispose();
            _iconOrange?.Dispose();
        };

        StartGracefulStopListener();
    }

    /// <summary>
    /// Waits on a named EventWaitHandle signalled by the deploy script.
    /// When signalled: writes a process handoff file, cancels workers (without killing
    /// child processes), then exits cleanly.
    /// </summary>
    private void StartGracefulStopListener()
    {
        var thread = new System.Threading.Thread(() =>
        {
            try
            {
                using var evt = new System.Threading.EventWaitHandle(
                    false,
                    System.Threading.EventResetMode.ManualReset,
                    "RevitWorkerApp_GracefulStop");

                evt.WaitOne();

                AppLog.Info("[GracefulStop] Deploy signal received — writing handoff and exiting");

                // Snapshot active processes BEFORE cancelling tokens
                var handoff = new ProcessHandoff();
                foreach (var kv in TaskRunner.ActiveProcesses)
                {
                    handoff.ActiveJobs.Add(new HandoffEntry
                    {
                        JobId      = kv.Key,
                        ProcessId  = kv.Value.Pid,
                        WorkerName = kv.Value.WorkerName,
                        CommandType = kv.Value.CommandType,
                        ModelPath  = kv.Value.ModelPath,
                        TimeoutSeconds = kv.Value.TimeoutSeconds,
                        StartedAt  = kv.Value.StartedAt.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ"),
                    });
                }

                if (handoff.ActiveJobs.Count > 0)
                {
                    try
                    {
                        handoff.Save();
                        AppLog.Info($"[GracefulStop] Handoff written: {handoff.ActiveJobs.Count} job(s) → {ProcessHandoff.FilePath}");
                    }
                    catch (Exception ex)
                    {
                        AppLog.Warning($"[GracefulStop] Could not write handoff: {ex.Message}");
                    }
                }
                else
                {
                    AppLog.Info("[GracefulStop] No active jobs — no handoff file written");
                }

                // Cancel worker loops without killing child processes
                _processManager.GracefulShutdown = true;
                _processManager.StopAll();

                // Exit on the UI thread
                PostToUI(() =>
                {
                    try { ExitThread(); } catch { }
                });
            }
            catch (Exception ex)
            {
                AppLog.Warning($"[GracefulStop] Listener error: {ex.Message}");
            }
        })
        {
            IsBackground = true,
            Name = "GracefulStopListener"
        };
        thread.Start();
    }

    public WorkerProcessManager ProcessManager => _processManager;
    public RedisMonitor RedisMonitor => _redisMonitor;

    public void RunWithSettingsShown()
    {
        EnsureMainForm();
        _mainForm!.Show();
        AutoConnect();
    }

    /// <summary>
    /// Automatically connect to Redis and start workers using saved settings.
    /// </summary>
    private void AutoConnect()
    {
        var settings = AppSettings.Load();
        if (string.IsNullOrWhiteSpace(settings.RedisUrl)) return;

        AppLog.Info($"Auto-connect: redis={settings.RedisUrl} queue={settings.QueueNames} workers={settings.ClampedWorkerCount} api={settings.ApiGatewayUrl ?? "(none)"}");
        try { settings.Save(); } catch { }

        Task.Run(() =>
        {
            try
            {
                _redisMonitor.Connect(settings.RedisUrl);
                _processManager.Configure(settings.RedisUrl, settings.QueueNames, settings.JobStartDelaySeconds);
                _processManager.SetConnection(_redisMonitor.Connection!);

                var count = settings.ClampedWorkerCount;
                for (var i = 0; i < count; i++)
                    _processManager.AddWorker();

                AppLog.Info($"Auto-connected OK, started {count} worker(s)");
                _processManager.AdoptHandoff();
                PostToUI(() => _mainForm?.OnAutoConnected());
            }
            catch (Exception ex)
            {
                AppLog.Warning($"Auto-connect failed: {ex.Message} \u2014 will retry in background");
                _redisMonitor.EnableAutoReconnect(settings.RedisUrl);
            }
        });
    }

    private void OnReconnected()
    {
        if (_paused) return;

        Task.Run(() =>
        {
            var settings = AppSettings.Load();
            var count = settings.ClampedWorkerCount;
            AppLog.Info($"Connection restored \u2014 starting {count} worker(s)...");

            try
            {
                _processManager.StopAll();
                _processManager.Configure(settings.RedisUrl, settings.QueueNames, settings.JobStartDelaySeconds);
                _processManager.SetConnection(_redisMonitor.Connection!);

                for (var i = 0; i < count; i++)
                    _processManager.AddWorker();

                AppLog.Info($"Reconnect: started {count} worker(s)");
            }
            catch (Exception ex)
            {
                AppLog.Error($"Reconnect: failed to restart workers \u2014 {ex.Message}");
            }
            PostToUI(UpdateIconAndTooltip);
        });
    }

    public bool IsPaused => _paused;

    public void SetMainForm(MainForm form) => _mainForm = form;

    private void EnsureMainForm()
    {
        if (_mainForm == null || _mainForm.IsDisposed)
        {
            _mainForm = new MainForm(this);
            _mainForm.FormClosing += (_, e) =>
            {
                if (e.CloseReason == CloseReason.UserClosing)
                {
                    e.Cancel = true;
                    _mainForm.Hide();
                }
            };
        }
    }

    private void TogglePause(ToolStripMenuItem pauseItem)
    {
        _paused = !_paused;
        if (_paused)
        {
            _processManager.StopAll();
            pauseItem.Text = "Resume";
        }
        else
        {
            pauseItem.Text = "Pause";
            var s = AppSettings.Load();
            if (!string.IsNullOrEmpty(s.RedisUrl) && _redisMonitor.IsConnected && _redisMonitor.Connection != null)
            {
                _processManager.Configure(s.RedisUrl, s.QueueNames, s.JobStartDelaySeconds);
                _processManager.SetConnection(_redisMonitor.Connection);
                for (var i = 0; i < s.ClampedWorkerCount; i++)
                    _processManager.AddWorker();
            }
        }
        UpdateIconAndTooltip();
    }

    private void OnWorkerStatusChanged(object? sender, WorkerStatusEventArgs e)
    {
        _anyWorkerBusy = _processManager.AnyBusy;
        PostToUI(() =>
        {
            UpdateIconAndTooltip();
            _mainForm?.UpdateWorkerRow(e.WorkerName, e.State, e.CurrentJobId, e.ModelPath);
        });
    }

    private void OnJobAccepted(object? sender, JobAcceptedEventArgs e)
    {
        PostToUI(() =>
        {
            UpdateIconAndTooltip();
            try
            {
                _notifyIcon.BalloonTipTitle = "Revit Worker";
                _notifyIcon.BalloonTipText = "Job accepted: " + e.JobId;
                _notifyIcon.BalloonTipIcon = ToolTipIcon.Info;
                _notifyIcon.ShowBalloonTip(3000);
            }
            catch { }
        });
    }

    private void PostToUI(Action action)
    {
        if (_uiContext != null)
        {
            _uiContext.Post(_ => { try { action(); } catch (ObjectDisposedException) { } }, null);
        }
        else if (_mainForm is { IsDisposed: false, IsHandleCreated: true })
        {
            try { _mainForm.BeginInvoke(action); } catch { }
        }
    }

    private void UpdateIconAndTooltip()
    {
        try
        {
            IconGenerator.TrayState state;
            string text;
            var ver = $"v{Program.Version}";
            if (_paused || !_redisMonitor.IsConnected)
            {
                state = IconGenerator.TrayState.Red;
                text = _paused ? $"Revit Worker {ver} (paused)" : $"Revit Worker {ver} (disconnected)";
            }
            else if (_anyWorkerBusy)
            {
                state = IconGenerator.TrayState.Orange;
                text = $"Revit Worker {ver} (working)";
            }
            else
            {
                state = IconGenerator.TrayState.Green;
                text = $"Revit Worker {ver} (active)";
            }

            _notifyIcon.Icon = state switch
            {
                IconGenerator.TrayState.Green => _iconGreen,
                IconGenerator.TrayState.Orange => _iconOrange,
                _ => _iconRed
            };
            _notifyIcon.Text = text;
        }
        catch (ObjectDisposedException) { }
    }

    private void Exit()
    {
        ExitThread();
    }
}
