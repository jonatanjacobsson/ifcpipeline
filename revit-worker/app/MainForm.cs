using System.Drawing;
using System.Windows.Forms;

namespace RevitWorkerApp;

public sealed class MainForm : Form
{
    private readonly TrayApplicationContext _trayContext;
    private TextBox _txtRedisUrl = null!;
    private TextBox _txtApiGatewayUrl = null!;
    private TextBox _txtApiKey = null!;
    private Button _btnConnect = null!;
    private Button _btnAddWorker = null!;
    private ListView _listWorkers = null!;
    private ListView _listQueues = null!;
    private Label _lblStatus = null!;
    private TextBox _txtLog = null!;
    private AppSettings _settings = null!;
    private System.Windows.Forms.Timer? _statsTimer;

    private static readonly Font UIFont = new("Segoe UI", 9f);
    private static readonly Font UIFontSmall = new("Segoe UI", 8f);
    private static readonly Color AccentGreen = Color.FromArgb(0x22, 0xc5, 0x5e);
    private static readonly Color AccentRed = Color.FromArgb(0xef, 0x44, 0x44);
    private static readonly Color BgColor = Color.FromArgb(0xfa, 0xfa, 0xfa);
    private static readonly Color PanelBg = Color.White;
    private static readonly Color ButtonBlue = Color.FromArgb(0x22, 0x8b, 0xe6);

    private const int Pad = 10;
    private const int InnerWidth = 310;

    public MainForm(TrayApplicationContext trayContext)
    {
        _trayContext = trayContext;
        _trayContext.SetMainForm(this);
        BuildForm();
        LoadSettings();
        SubscribeToEvents();
    }

    private void BuildForm()
    {
        Text = "Revit Worker";
        FormBorderStyle = FormBorderStyle.Sizable;
        MaximizeBox = false;
        MinimizeBox = true;
        ShowInTaskbar = true;
        BackColor = BgColor;
        Font = UIFont;
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(350, 570);
        ClientSize = new Size(InnerWidth + 2 * Pad, 670);

        var y = Pad;

        // --- Connection ---
        var lblConn = new Label
        {
            Text = "CONNECTION",
            Font = UIFontSmall,
            ForeColor = Color.Gray,
            Location = new Point(Pad, y),
            AutoSize = true
        };
        Controls.Add(lblConn);
        y += 18;

        var connPanel = new Panel
        {
            Location = new Point(Pad, y),
            Size = new Size(InnerWidth, 36),
            BackColor = PanelBg,
            BorderStyle = BorderStyle.FixedSingle,
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        };
        Controls.Add(connPanel);

        _txtRedisUrl = new TextBox
        {
            Location = new Point(6, 6),
            Size = new Size(InnerWidth - 82, 22),
            BorderStyle = BorderStyle.None,
            Font = UIFont,
            PlaceholderText = "redis://host:6379/0",
            BackColor = PanelBg,
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        };
        connPanel.Controls.Add(_txtRedisUrl);

        _btnConnect = new Button
        {
            Text = "Connect",
            Size = new Size(70, 32),
            FlatStyle = FlatStyle.Flat,
            BackColor = ButtonBlue,
            ForeColor = Color.White,
            Font = UIFontSmall,
            Cursor = Cursors.Hand,
            Anchor = AnchorStyles.Top | AnchorStyles.Right
        };
        _btnConnect.Location = new Point(connPanel.Width - _btnConnect.Width - 2, 1);
        _btnConnect.FlatAppearance.BorderSize = 0;
        _btnConnect.Click += BtnConnect_Click;
        connPanel.Controls.Add(_btnConnect);

        y += 44;

        _lblStatus = new Label
        {
            Text = "\u25cf Disconnected",
            Font = UIFontSmall,
            ForeColor = AccentRed,
            Location = new Point(Pad, y),
            AutoSize = true
        };
        Controls.Add(_lblStatus);
        y += 22;

        // --- API Gateway (optional, for log uploads) ---
        var lblApi = new Label
        {
            Text = "API GATEWAY (optional — for log uploads)",
            Font = UIFontSmall,
            ForeColor = Color.Gray,
            Location = new Point(Pad, y),
            AutoSize = true
        };
        Controls.Add(lblApi);
        y += 18;

        var apiPanel = new Panel
        {
            Location = new Point(Pad, y),
            Size = new Size(InnerWidth, 62),
            BackColor = PanelBg,
            BorderStyle = BorderStyle.FixedSingle,
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        };
        Controls.Add(apiPanel);

        _txtApiGatewayUrl = new TextBox
        {
            Location = new Point(6, 4),
            Size = new Size(InnerWidth - 14, 22),
            BorderStyle = BorderStyle.None,
            Font = UIFont,
            PlaceholderText = "http://bim-host-ubnt:8000",
            BackColor = PanelBg,
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        };
        apiPanel.Controls.Add(_txtApiGatewayUrl);

        _txtApiKey = new TextBox
        {
            Location = new Point(6, 32),
            Size = new Size(InnerWidth - 14, 22),
            BorderStyle = BorderStyle.None,
            Font = UIFont,
            PlaceholderText = "API key",
            BackColor = PanelBg,
            UseSystemPasswordChar = true,
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        };
        apiPanel.Controls.Add(_txtApiKey);

        y += 70;

        // --- Queue stats ---
        var lblQueues = new Label
        {
            Text = "QUEUE",
            Font = UIFontSmall,
            ForeColor = Color.Gray,
            Location = new Point(Pad, y),
            AutoSize = true
        };
        Controls.Add(lblQueues);
        y += 16;

        _listQueues = new ListView
        {
            Location = new Point(Pad, y),
            Size = new Size(InnerWidth, 40),
            View = View.Details,
            FullRowSelect = true,
            Font = UIFontSmall,
            BackColor = PanelBg,
            BorderStyle = BorderStyle.FixedSingle,
            HeaderStyle = ColumnHeaderStyle.Nonclickable,
            GridLines = true,
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        };
        _listQueues.Columns.Add("Queue", 70);
        _listQueues.Columns.Add("Queued", 50, HorizontalAlignment.Right);
        _listQueues.Columns.Add("Started", 50, HorizontalAlignment.Right);
        _listQueues.Columns.Add("Failed", 50, HorizontalAlignment.Right);
        _listQueues.Columns.Add("Finished", 55, HorizontalAlignment.Right);
        Controls.Add(_listQueues);
        y += 46;

        // --- Workers header + add button ---
        var lblWorkers = new Label
        {
            Text = "WORKERS",
            Font = UIFontSmall,
            ForeColor = Color.Gray,
            Location = new Point(Pad, y + 4),
            AutoSize = true
        };
        Controls.Add(lblWorkers);

        _btnAddWorker = new Button
        {
            Text = "+ Add Worker",
            Size = new Size(90, 24),
            FlatStyle = FlatStyle.Flat,
            BackColor = AccentGreen,
            ForeColor = Color.White,
            Font = UIFontSmall,
            Cursor = Cursors.Hand,
            Enabled = false,
            Anchor = AnchorStyles.Top | AnchorStyles.Right
        };
        _btnAddWorker.Location = new Point(Pad + InnerWidth - _btnAddWorker.Width, y);
        _btnAddWorker.FlatAppearance.BorderSize = 0;
        _btnAddWorker.Click += BtnAddWorker_Click;
        Controls.Add(_btnAddWorker);
        y += 28;

        // --- Worker list ---
        var listHeight = 100;
        _listWorkers = new ListView
        {
            Location = new Point(Pad, y),
            Size = new Size(InnerWidth, listHeight),
            View = View.Details,
            FullRowSelect = true,
            Font = UIFontSmall,
            BackColor = PanelBg,
            BorderStyle = BorderStyle.FixedSingle,
            HeaderStyle = ColumnHeaderStyle.Nonclickable,
            GridLines = true,
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        };
        _listWorkers.Columns.Add("Worker", 120);
        _listWorkers.Columns.Add("State", 60);
        _listWorkers.Columns.Add("Job", 90);
        _listWorkers.Columns.Add("", 30);
        Controls.Add(_listWorkers);
        y += listHeight + 6;

        // --- Log panel ---
        var lblLog = new Label
        {
            Text = "LOG",
            Font = UIFontSmall,
            ForeColor = Color.Gray,
            Location = new Point(Pad, y),
            AutoSize = true
        };
        Controls.Add(lblLog);
        y += 16;

        _txtLog = new TextBox
        {
            Location = new Point(Pad, y),
            Size = new Size(InnerWidth, ClientSize.Height - y - Pad),
            Multiline = true,
            ReadOnly = true,
            ScrollBars = ScrollBars.Vertical,
            Font = new Font("Consolas", 7.5f),
            BackColor = Color.FromArgb(0x1e, 0x1e, 0x1e),
            ForeColor = Color.FromArgb(0xcc, 0xcc, 0xcc),
            BorderStyle = BorderStyle.FixedSingle,
            WordWrap = false,
            Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        };
        Controls.Add(_txtLog);

        AppLog.LogAdded += line =>
        {
            if (InvokeRequired)
            {
                try { BeginInvoke(() => AppendLog(line)); } catch { }
                return;
            }
            AppendLog(line);
        };

        var ctxMenu = new ContextMenuStrip();
        var stopItem = new ToolStripMenuItem("Stop Worker");
        stopItem.Click += (_, _) =>
        {
            if (_listWorkers.SelectedItems.Count > 0)
            {
                var name = _listWorkers.SelectedItems[0].Text;
                if (name != "ERROR")
                    _trayContext.ProcessManager.StopWorker(name);
            }
        };
        ctxMenu.Items.Add(stopItem);
        _listWorkers.ContextMenuStrip = ctxMenu;

        _listWorkers.MouseClick += (_, e) =>
        {
            if (e.Button != MouseButtons.Left) return;
            var hit = _listWorkers.HitTest(e.Location);
            if (hit.Item != null && hit.SubItem != null && hit.Item.SubItems.IndexOf(hit.SubItem) == 3)
            {
                var name = hit.Item.Text;
                if (name != "ERROR")
                    _trayContext.ProcessManager.StopWorker(name);
            }
        };
    }

    private void LoadSettings()
    {
        _settings = AppSettings.Load();
        _txtRedisUrl.Text = _settings.RedisUrl;
        _txtApiGatewayUrl.Text = _settings.ApiGatewayUrl ?? "";
        _txtApiKey.Text = _settings.ApiKey ?? "";
    }

    private void SubscribeToEvents()
    {
        _trayContext.RedisMonitor.ConnectionStateChanged += (_, _) =>
        {
            if (InvokeRequired) { BeginInvoke(UpdateStatusLabel); return; }
            UpdateStatusLabel();
        };
        _trayContext.ProcessManager.WorkerError += (_, e) =>
        {
            if (InvokeRequired) { BeginInvoke(() => ShowWorkerError(e.Message)); return; }
            ShowWorkerError(e.Message);
        };
    }

    private void UpdateStatusLabel()
    {
        var connected = _trayContext.RedisMonitor.IsConnected;
        if (connected)
        {
            _lblStatus.Text = "\u25cf Connected";
            _lblStatus.ForeColor = AccentGreen;
        }
        else
        {
            _lblStatus.Text = "\u25cf Disconnected";
            _lblStatus.ForeColor = AccentRed;
        }
        _btnAddWorker.Enabled = connected;
    }

    public void UpdateWorkerRow(string workerName, string state, string? jobId)
    {
        if (IsDisposed) return;
        if (state == "stopped")
        {
            var toRemove = _listWorkers.Items.Cast<ListViewItem>().FirstOrDefault(i => i.Text == workerName);
            if (toRemove != null) _listWorkers.Items.Remove(toRemove);
            SaveCurrentWorkerCount();
            return;
        }

        var existing = _listWorkers.Items.Cast<ListViewItem>().FirstOrDefault(i => i.Text == workerName);
        if (existing != null)
        {
            existing.SubItems[1].Text = state;
            existing.SubItems[2].Text = jobId ?? "";
        }
        else
        {
            var item = new ListViewItem(workerName);
            item.SubItems.Add(state);
            item.SubItems.Add(jobId ?? "");
            item.SubItems.Add("\u00d7");
            _listWorkers.Items.Add(item);
        }
    }

    private void ShowWorkerError(string message)
    {
        var item = new ListViewItem("ERROR");
        item.SubItems.Add("");
        item.SubItems.Add(message.Length > 60 ? message[..60] + "..." : message);
        item.SubItems.Add("");
        item.ForeColor = Color.Red;
        _listWorkers.Items.Add(item);
        if (_listWorkers.Items.Count > 50)
            _listWorkers.Items.RemoveAt(0);
        _listWorkers.EnsureVisible(_listWorkers.Items.Count - 1);
    }

    private void BtnConnect_Click(object? sender, EventArgs e)
    {
        var url = _txtRedisUrl.Text.Trim();
        if (string.IsNullOrEmpty(url))
        {
            MessageBox.Show("Please enter a Redis URL.", Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }

        _btnConnect.Enabled = false;
        _btnConnect.Text = "...";
        _listWorkers.Items.Clear();
        try
        {
            _trayContext.ProcessManager.StopAll();
            _trayContext.RedisMonitor.Connect(url);
            _settings.RedisUrl = url;
            _settings.QueueNames = AppSettings.DefaultQueueNames;
            _settings.ApiGatewayUrl = string.IsNullOrWhiteSpace(_txtApiGatewayUrl.Text) ? null : _txtApiGatewayUrl.Text.Trim();
            _settings.ApiKey = string.IsNullOrWhiteSpace(_txtApiKey.Text) ? null : _txtApiKey.Text.Trim();
            _settings.Save();

            _trayContext.ProcessManager.Configure(url, _settings.QueueNames);
            _trayContext.ProcessManager.SetConnection(_trayContext.RedisMonitor.Connection!);

            _btnConnect.Text = "Reconnect";
            _btnConnect.Enabled = true;
            UpdateStatusLabel();
            StartStatsTimer();
        }
        catch (Exception ex)
        {
            _btnConnect.Text = "Connect";
            _btnConnect.Enabled = true;
            UpdateStatusLabel();
            MessageBox.Show("Failed to connect:\n" + ex.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private void BtnAddWorker_Click(object? sender, EventArgs e)
    {
        var name = _trayContext.ProcessManager.AddWorker();
        if (name == null)
        {
            MessageBox.Show(
                _trayContext.RedisMonitor.IsConnected
                    ? $"Maximum of {AppSettings.MaxWorkerCount} workers reached."
                    : "Connect to Redis first.",
                Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        else
        {
            SaveCurrentWorkerCount();
        }
    }

    private void SaveCurrentWorkerCount()
    {
        _settings.WorkerCount = _trayContext.ProcessManager.CurrentCount;
        try { _settings.Save(); } catch { }
    }

    private void StartStatsTimer()
    {
        if (_statsTimer != null) return;
        _statsTimer = new System.Windows.Forms.Timer { Interval = 3000 };
        _statsTimer.Tick += (_, _) => RefreshQueueStats();
        _statsTimer.Start();
        RefreshQueueStats();
    }

    private void RefreshQueueStats()
    {
        var conn = _trayContext.RedisMonitor.Connection;
        if (conn == null || !conn.IsConnected) return;

        try
        {
            var db = conn.GetDatabase();
            var queues = (_settings.QueueNames ?? AppSettings.DefaultQueueNames)
                .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

            _listQueues.BeginUpdate();
            _listQueues.Items.Clear();
            var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            foreach (var q in queues)
            {
                var queued = db.ListLength($"rq:queue:{q}");
                var started = db.SortedSetLength($"rq:wip:{q}", now, double.PositiveInfinity);
                var failed = db.SortedSetLength($"rq:failed:{q}");
                var finished = db.SortedSetLength($"rq:finished:{q}");

                var item = new ListViewItem(q);
                item.SubItems.Add(queued.ToString());
                item.SubItems.Add(started.ToString());
                item.SubItems.Add(failed.ToString());
                item.SubItems.Add(finished.ToString());
                _listQueues.Items.Add(item);
            }
            _listQueues.EndUpdate();
        }
        catch { }
    }

    private void AppendLog(string line)
    {
        if (_txtLog.IsDisposed) return;
        if (_txtLog.TextLength > 60_000)
            _txtLog.Text = _txtLog.Text.Substring(_txtLog.TextLength - 40_000);
        _txtLog.AppendText(line + Environment.NewLine);
    }

    protected override void OnShown(EventArgs e)
    {
        base.OnShown(e);
        foreach (var line in AppLog.GetBuffer())
            AppendLog(line);
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _statsTimer?.Stop();
            _statsTimer?.Dispose();
        }
        base.Dispose(disposing);
    }
}
