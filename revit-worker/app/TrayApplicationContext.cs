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
            Text = "Revit Worker (disconnected)",
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
        _redisMonitor.ConnectionStateChanged += (_, _) => PostToUI(UpdateIconAndTooltip);

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
    }

    public WorkerProcessManager ProcessManager => _processManager;
    public RedisMonitor RedisMonitor => _redisMonitor;

    public void RunWithSettingsShown()
    {
        EnsureMainForm();
        _mainForm!.Show();
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
                _processManager.Configure(s.RedisUrl, s.QueueNames);
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
            _mainForm?.UpdateWorkerRow(e.WorkerName, e.State, e.CurrentJobId);
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
            _uiContext.Post(_ => action(), null);
        else
            action();
    }

    private void UpdateIconAndTooltip()
    {
        try
        {
            IconGenerator.TrayState state;
            string text;
            if (_paused || !_redisMonitor.IsConnected)
            {
                state = IconGenerator.TrayState.Red;
                text = _paused ? "Revit Worker (paused)" : "Revit Worker (disconnected)";
            }
            else if (_anyWorkerBusy)
            {
                state = IconGenerator.TrayState.Orange;
                text = "Revit Worker (working)";
            }
            else
            {
                state = IconGenerator.TrayState.Green;
                text = "Revit Worker (active)";
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
