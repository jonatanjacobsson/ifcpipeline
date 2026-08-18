using System.Drawing;
using System.Drawing.Drawing2D;
using System.Text.RegularExpressions;
using System.Windows.Forms;

namespace RevitWorkerApp;

public sealed class MainForm : Form
{
    private readonly TrayApplicationContext _trayContext;

    private TextBox _txtRedisUrl = null!;
    private TextBox _txtApiGatewayUrl = null!;
    private TextBox _txtApiKey = null!;
    private Button _btnConnect = null!;
    private Label _lblStatus = null!;
    private Button _btnAddWorker = null!;
    private Button _btnToggleLog = null!;
    private FlowLayoutPanel _workerGrid = null!;
    private SplitContainer _splitMain = null!;
    private TextBox _txtLog = null!;
    private Label _lblLogTitle = null!;
    private Label? _btnClearFilter;
    private Label? _btnWrapToggle;
    private QueueStatsBar _queueStats = null!;

    private AppSettings _settings = null!;
    private System.Windows.Forms.Timer? _statsTimer;
    private System.Windows.Forms.Timer? _durationTimer;
    private string? _selectedWorker;
    private readonly List<string> _allLogLines = new();
    private readonly Dictionary<string, WorkerCard> _workerCards = new();
    private readonly Dictionary<string, AdoptedJobCard> _adoptedCards = new();
    private bool _logVisible = true;
    private bool _initialized;

    private static readonly Font SectionFont = new("Segoe UI", 8f, FontStyle.Bold);
    private static readonly Font UIFont = new("Segoe UI", 9f);
    private static readonly Font UIFontSmall = new("Segoe UI", 8f);
    private static readonly Font LogFont = new("Consolas", 8f);

    private static readonly Color AccentGreen = Color.FromArgb(0x16, 0xA3, 0x4A);
    private static readonly Color AccentOrange = Color.FromArgb(0xEA, 0x88, 0x0F);
    private static readonly Color AccentAmber = Color.FromArgb(0xD9, 0x77, 0x06);
    private static readonly Color AccentRed = Color.FromArgb(0xDC, 0x26, 0x26);
    private static readonly Color AccentBlue = Color.FromArgb(0x25, 0x63, 0xEB);
    private static readonly Color BgColor = Color.FromArgb(0xF1, 0xF5, 0xF9);
    private static readonly Color CardBorder = Color.FromArgb(0xE2, 0xE8, 0xF0);
    private static readonly Color SelectedBorder = Color.FromArgb(0x25, 0x63, 0xEB);
    private static readonly Color SectionLabelColor = Color.FromArgb(0x64, 0x74, 0x8B);
    private static readonly Color LogBg = Color.FromArgb(0x0F, 0x17, 0x2A);
    private static readonly Color LogFg = Color.FromArgb(0xCB, 0xD5, 0xE1);
    private static readonly Color Muted = Color.FromArgb(0x94, 0xA3, 0xB8);

    private const int Pad = 14;

    public MainForm(TrayApplicationContext trayContext)
    {
        _trayContext = trayContext;
        SuspendLayout();
        BuildForm();
        ResumeLayout(true);
        _initialized = true;
        _trayContext.SetMainForm(this);
        LoadSettings();
        SubscribeToEvents();
        StartDurationTimer();
    }

    // ──────────────────────────────────────────────
    //  Form construction
    // ──────────────────────────────────────────────

    private void BuildForm()
    {
        Text = $"Revit Worker v{Program.Version}";
        FormBorderStyle = FormBorderStyle.Sizable;
        BackColor = BgColor;
        Font = UIFont;
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(600, 480);
        ClientSize = new Size(980, 640);
        SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.AllPaintingInWmPaint, true);

        BuildMainArea();
        BuildWorkerHeader();
        BuildConfigSection();
    }

    private void BuildConfigSection()
    {
        var formW = ClientSize.Width;

        var outer = new Panel
        {
            Dock = DockStyle.Top,
            BackColor = BgColor,
            Padding = new Padding(Pad, Pad / 2, Pad, 4),
            Width = formW,
        };

        var cardW = formW - 2 * Pad;
        var card = new Panel
        {
            Dock = DockStyle.Fill,
            BackColor = Color.White,
            Width = cardW,
        };
        card.Paint += (_, pe) =>
        {
            using var pen = new Pen(CardBorder);
            pe.Graphics.DrawRectangle(pen, 0, 0, card.Width - 1, card.Height - 1);
        };

        var cw = cardW - 2;
        var y = 14;

        var lblConn = new Label
        {
            Text = "CONNECTION",
            Font = SectionFont,
            ForeColor = SectionLabelColor,
            Location = new Point(14, y),
            AutoSize = true,
        };
        card.Controls.Add(lblConn);
        y += 20;

        _btnConnect = new Button
        {
            Text = "Connect",
            Size = new Size(88, 26),
            FlatStyle = FlatStyle.Flat,
            BackColor = AccentBlue,
            ForeColor = Color.White,
            Font = UIFontSmall,
            Cursor = Cursors.Hand,
            Anchor = AnchorStyles.Top | AnchorStyles.Right,
        };
        _btnConnect.FlatAppearance.BorderSize = 0;
        _btnConnect.Location = new Point(cw - _btnConnect.Width - 12, y);
        _btnConnect.Click += BtnConnect_Click;
        card.Controls.Add(_btnConnect);

        _txtRedisUrl = new TextBox
        {
            Location = new Point(14, y + 1),
            Size = new Size(cw - 14 - _btnConnect.Width - 22, 24),
            Font = UIFontSmall,
            PlaceholderText = "redis://host:6379/0",
            BorderStyle = BorderStyle.FixedSingle,
            BackColor = Color.White,
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right,
        };
        card.Controls.Add(_txtRedisUrl);
        y += 32;

        _lblStatus = new Label
        {
            Text = "\u25cf Disconnected",
            Font = UIFontSmall,
            ForeColor = AccentRed,
            Location = new Point(14, y),
            AutoSize = true,
        };
        card.Controls.Add(_lblStatus);
        y += 22;

        card.Controls.Add(MakeDivider(14, y, cw - 28));
        y += 10;

        var lblApi = new Label
        {
            Text = "API GATEWAY",
            Font = SectionFont,
            ForeColor = SectionLabelColor,
            Location = new Point(14, y),
            AutoSize = true,
        };
        card.Controls.Add(lblApi);
        y += 20;

        _txtApiGatewayUrl = new TextBox
        {
            Location = new Point(14, y),
            Size = new Size((cw - 38) / 2, 24),
            Font = UIFontSmall,
            PlaceholderText = "http://host:8000",
            BorderStyle = BorderStyle.FixedSingle,
            BackColor = Color.White,
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right,
        };
        card.Controls.Add(_txtApiGatewayUrl);

        _txtApiKey = new TextBox
        {
            Location = new Point(14 + (cw - 38) / 2 + 10, y),
            Size = new Size((cw - 38) / 2, 24),
            Font = UIFontSmall,
            PlaceholderText = "API key",
            BorderStyle = BorderStyle.FixedSingle,
            BackColor = Color.White,
            UseSystemPasswordChar = true,
            Anchor = AnchorStyles.Top | AnchorStyles.Right,
        };
        card.Controls.Add(_txtApiKey);
        y += 30;

        card.Controls.Add(MakeDivider(14, y, cw - 28));
        y += 10;

        _queueStats = new QueueStatsBar
        {
            Location = new Point(14, y),
            Size = new Size(cw - 28, 22),
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right,
        };
        card.Controls.Add(_queueStats);
        y += 28;

        var cardHeight = y + 6;
        card.Height = cardHeight;
        outer.Height = cardHeight + Pad / 2 + 4;

        outer.Controls.Add(card);
        Controls.Add(outer);
    }

    private void BuildWorkerHeader()
    {
        var formW = ClientSize.Width;
        var header = new Panel
        {
            Dock = DockStyle.Top,
            Height = 40,
            Width = formW,
            BackColor = BgColor,
        };

        var lbl = new Label
        {
            Text = "WORKERS",
            Font = SectionFont,
            ForeColor = SectionLabelColor,
            Location = new Point(Pad, 12),
            AutoSize = true,
        };
        header.Controls.Add(lbl);

        _btnToggleLog = new Button
        {
            Text = "\u25c0 Log",
            Size = new Size(64, 28),
            FlatStyle = FlatStyle.Flat,
            BackColor = Color.FromArgb(0x47, 0x55, 0x69),
            ForeColor = Color.White,
            Font = UIFontSmall,
            Cursor = Cursors.Hand,
            Anchor = AnchorStyles.Top | AnchorStyles.Right,
        };
        _btnToggleLog.FlatAppearance.BorderSize = 0;
        _btnToggleLog.Location = new Point(formW - Pad - _btnToggleLog.Width, 6);
        _btnToggleLog.Click += (_, _) => ToggleLogPanel();
        header.Controls.Add(_btnToggleLog);

        _btnAddWorker = new Button
        {
            Text = "+ Add Worker",
            Size = new Size(100, 28),
            FlatStyle = FlatStyle.Flat,
            BackColor = AccentGreen,
            ForeColor = Color.White,
            Font = UIFontSmall,
            Cursor = Cursors.Hand,
            Enabled = false,
            Anchor = AnchorStyles.Top | AnchorStyles.Right,
        };
        _btnAddWorker.FlatAppearance.BorderSize = 0;
        _btnAddWorker.Location = new Point(_btnToggleLog.Left - _btnAddWorker.Width - 8, 6);
        _btnAddWorker.Click += BtnAddWorker_Click;
        header.Controls.Add(_btnAddWorker);

        Controls.Add(header);
    }

    private void BuildMainArea()
    {
        _splitMain = new SplitContainer
        {
            Dock = DockStyle.Fill,
            Orientation = Orientation.Vertical,
            BackColor = BgColor,
            SplitterWidth = 4,
            SplitterDistance = 580,
            Panel1MinSize = 25,
            Panel2MinSize = 25,
        };

        _workerGrid = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            AutoScroll = true,
            BackColor = BgColor,
            Padding = new Padding(Pad - 6, 6, Pad - 6, 6),
            WrapContents = true,
        };
        _splitMain.Panel1.Controls.Add(_workerGrid);

        BuildLogPanel();

        AppLog.LogAdded += line =>
        {
            if (IsDisposed) return;
            if (InvokeRequired)
            {
                try { BeginInvoke(() => AppendLog(line)); }
                catch { }
                return;
            }
            AppendLog(line);
        };

        Controls.Add(_splitMain);
    }

    private void BuildLogPanel()
    {
        var logHeader = new Panel
        {
            Dock = DockStyle.Top,
            Height = 34,
            BackColor = Color.FromArgb(0x1E, 0x29, 0x3B),
        };

        _lblLogTitle = new Label
        {
            Text = "LOG",
            Font = SectionFont,
            ForeColor = Color.FromArgb(0x94, 0xA3, 0xB8),
            Location = new Point(12, 10),
            AutoSize = true,
        };
        logHeader.Controls.Add(_lblLogTitle);

        _btnWrapToggle = new Label
        {
            Text = "wrap",
            Font = UIFontSmall,
            ForeColor = Color.FromArgb(0x64, 0x74, 0x8B),
            Cursor = Cursors.Hand,
            AutoSize = true,
        };
        _btnWrapToggle.Click += (_, _) => ToggleWordWrap();
        logHeader.Controls.Add(_btnWrapToggle);

        _btnClearFilter = new Label
        {
            Text = "\u2715 clear",
            Font = UIFontSmall,
            ForeColor = Color.FromArgb(0x64, 0x74, 0x8B),
            Cursor = Cursors.Hand,
            AutoSize = true,
            Visible = false,
        };
        _btnClearFilter.Click += (_, _) => SelectWorkerCard(null);
        logHeader.Controls.Add(_btnClearFilter);

        _splitMain.Panel2.Resize += (_, _) => PositionLogHeaderButtons();

        _txtLog = new TextBox
        {
            Dock = DockStyle.Fill,
            Multiline = true,
            ReadOnly = true,
            ScrollBars = ScrollBars.Both,
            Font = LogFont,
            BackColor = LogBg,
            ForeColor = LogFg,
            BorderStyle = BorderStyle.None,
            WordWrap = false,
        };

        _splitMain.Panel2.Controls.Add(_txtLog);
        _splitMain.Panel2.Controls.Add(logHeader);
    }

    private static Panel MakeDivider(int x, int y, int width)
    {
        return new Panel
        {
            Location = new Point(x, y),
            Size = new Size(width, 1),
            BackColor = CardBorder,
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right,
        };
    }

    // ──────────────────────────────────────────────
    //  Public API (called from TrayApplicationContext)
    // ──────────────────────────────────────────────

    public void OnAutoConnected()
    {
        if (!_initialized) return;
        if (InvokeRequired) { try { BeginInvoke(OnAutoConnected); } catch { } return; }
        _btnConnect.Text = "Reconnect";
        UpdateStatusLabel();
        StartStatsTimer();
    }

    public void UpdateWorkerRow(string workerName, string state, string? jobId, string? modelPath = null)
    {
        if (!_initialized || IsDisposed) return;
        if (InvokeRequired)
        {
            try { BeginInvoke(() => UpdateWorkerRow(workerName, state, jobId, modelPath)); }
            catch { }
            return;
        }

        if (state == "stopped")
        {
            RemoveWorkerCard(workerName);
            SaveCurrentWorkerCount();
            return;
        }

        if (_workerCards.TryGetValue(workerName, out var card))
            card.UpdateState(state, jobId, modelPath);
        else
            AddWorkerCard(workerName, state, jobId, modelPath);
    }

    // ──────────────────────────────────────────────
    //  Adopted job card management
    // ──────────────────────────────────────────────

    public void OnAdoptedJobStarted(AdoptedJobEventArgs e)
    {
        if (!_initialized || IsDisposed) return;
        if (InvokeRequired) { try { BeginInvoke(() => OnAdoptedJobStarted(e)); } catch { } return; }

        if (_adoptedCards.ContainsKey(e.JobId)) return;
        var card = new AdoptedJobCard(e.JobId, e.OriginalWorkerName, e.Pid, e.AdoptedAt, e.ModelPath);
        _adoptedCards[e.JobId] = card;
        _workerGrid.Controls.Add(card);
    }

    public void OnAdoptedJobFinished(AdoptedJobEventArgs e)
    {
        if (!_initialized || IsDisposed) return;
        if (InvokeRequired) { try { BeginInvoke(() => OnAdoptedJobFinished(e)); } catch { } return; }

        if (!_adoptedCards.TryGetValue(e.JobId, out var card)) return;
        card.MarkDone(e.Success);
        // Remove the card after a short delay so the user can see the final state
        var t = new System.Windows.Forms.Timer { Interval = 4000 };
        t.Tick += (_, _) =>
        {
            t.Stop(); t.Dispose();
            _workerGrid.Controls.Remove(card);
            _adoptedCards.Remove(e.JobId);
            card.Dispose();
        };
        t.Start();
    }

    // ──────────────────────────────────────────────
    //  Worker card management
    // ──────────────────────────────────────────────

    private void AddWorkerCard(string workerName, string state, string? jobId, string? modelPath = null)
    {
        var card = new WorkerCard(workerName);
        card.UpdateState(state, jobId, modelPath);
        card.StopRequested += c => _trayContext.ProcessManager.StopWorker(c.WorkerName);
        card.Clicked += c => SelectWorkerCard(c.IsSelected ? null : c);
        _workerCards[workerName] = card;
        _workerGrid.Controls.Add(card);
    }

    private void RemoveWorkerCard(string workerName)
    {
        if (!_workerCards.TryGetValue(workerName, out var card)) return;
        if (_selectedWorker == workerName)
            SelectWorkerCard(null);
        _workerGrid.Controls.Remove(card);
        _workerCards.Remove(workerName);
        card.Dispose();
    }

    private void ClearAllWorkerCards()
    {
        _selectedWorker = null;
        foreach (var card in _workerCards.Values)
            card.Dispose();
        _workerCards.Clear();
        foreach (var card in _adoptedCards.Values)
            card.Dispose();
        _adoptedCards.Clear();
        _workerGrid.Controls.Clear();
        UpdateLogTitle();
    }

    private void SelectWorkerCard(WorkerCard? card)
    {
        if (_selectedWorker != null && _workerCards.TryGetValue(_selectedWorker, out var prev))
            prev.IsSelected = false;

        if (card != null && !card.IsSelected)
        {
            card.IsSelected = true;
            _selectedWorker = card.WorkerName;
        }
        else
        {
            _selectedWorker = null;
        }

        UpdateLogTitle();
        RebuildLog();
    }

    // ──────────────────────────────────────────────
    //  Log management
    // ──────────────────────────────────────────────

    private void AppendLog(string line)
    {
        if (_txtLog == null || _txtLog.IsDisposed) return;

        _allLogLines.Add(line);
        if (_allLogLines.Count > 5000)
            _allLogLines.RemoveRange(0, 1000);

        if (!MatchesFilter(line)) return;

        if (_txtLog.TextLength > 80_000)
            RebuildLog();
        else
            _txtLog.AppendText(line + Environment.NewLine);
    }

    private bool MatchesFilter(string line)
    {
        return _selectedWorker == null || line.Contains($"[{_selectedWorker}]");
    }

    private void RebuildLog()
    {
        if (_txtLog == null || _txtLog.IsDisposed) return;

        IEnumerable<string> lines = _selectedWorker != null
            ? _allLogLines.Where(l => l.Contains($"[{_selectedWorker}]"))
            : _allLogLines;

        _txtLog.Text = string.Join(Environment.NewLine, lines.TakeLast(3000));
        if (_txtLog.TextLength > 0)
        {
            _txtLog.SelectionStart = _txtLog.TextLength;
            _txtLog.ScrollToCaret();
        }
    }

    private void UpdateLogTitle()
    {
        if (_lblLogTitle == null) return;
        _lblLogTitle.Text = _selectedWorker != null ? $"LOG \u2014 {_selectedWorker}" : "LOG";
        if (_btnClearFilter != null)
            _btnClearFilter.Visible = _selectedWorker != null;
    }

    private void ToggleWordWrap()
    {
        if (_txtLog == null) return;
        _txtLog.WordWrap = !_txtLog.WordWrap;
        _txtLog.ScrollBars = _txtLog.WordWrap ? ScrollBars.Vertical : ScrollBars.Both;
        if (_btnWrapToggle != null)
            _btnWrapToggle.ForeColor = _txtLog.WordWrap ? AccentBlue : Color.FromArgb(0x64, 0x74, 0x8B);
    }

    private void PositionLogHeaderButtons()
    {
        var w = _splitMain.Panel2.Width;
        if (_btnWrapToggle != null)
            _btnWrapToggle.Location = new Point(Math.Max(60, w - _btnWrapToggle.Width - 12), 10);
        if (_btnClearFilter != null)
            _btnClearFilter.Location = new Point(
                Math.Max(60, w - (_btnWrapToggle?.Width ?? 0) - _btnClearFilter.Width - 20), 10);
    }

    private void ToggleLogPanel()
    {
        _logVisible = !_logVisible;
        _btnToggleLog.Text = _logVisible ? "\u25c0 Log" : "Log \u25b6";

        if (_logVisible)
        {
            _splitMain.Panel2Collapsed = false;
            try
            {
                _splitMain.Panel1MinSize = 280;
                _splitMain.Panel2MinSize = 240;
                _splitMain.SplitterDistance = Math.Max(280, _splitMain.Width - 380);
            }
            catch { }
            if (Width < 850)
                Width = Math.Min(1000, Screen.PrimaryScreen?.WorkingArea.Width ?? 1000);
            RebuildLog();
        }
        else
        {
            _splitMain.Panel2Collapsed = true;
            _splitMain.Panel1MinSize = 25;
            _splitMain.Panel2MinSize = 25;
        }
    }

    // ──────────────────────────────────────────────
    //  Event handlers
    // ──────────────────────────────────────────────

    private void BtnConnect_Click(object? sender, EventArgs e)
    {
        var url = _txtRedisUrl.Text.Trim();
        if (string.IsNullOrEmpty(url))
        {
            MessageBox.Show("Please enter a Redis URL.", Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }

        _btnConnect.Enabled = false;
        _btnConnect.Text = "\u2026";
        ClearAllWorkerCards();

        var apiUrl = string.IsNullOrWhiteSpace(_txtApiGatewayUrl.Text) ? null : _txtApiGatewayUrl.Text.Trim();
        var apiKey = string.IsNullOrWhiteSpace(_txtApiKey.Text) ? null : _txtApiKey.Text.Trim();

        Task.Run(() =>
        {
            try
            {
                _trayContext.ProcessManager.StopAll();
                _trayContext.RedisMonitor.Connect(url);

                BeginInvoke(() =>
                {
                    _settings.RedisUrl = url;
                    _settings.QueueNames = AppSettings.DefaultQueueNames;
                    _settings.ApiGatewayUrl = apiUrl;
                    _settings.ApiKey = apiKey;
                    _settings.Save();

                    _trayContext.ProcessManager.Configure(url, _settings.QueueNames, _settings.JobStartDelaySeconds);
                    _trayContext.ProcessManager.SetConnection(_trayContext.RedisMonitor.Connection!);

                    _btnConnect.Text = "Reconnect";
                    _btnConnect.Enabled = true;
                    UpdateStatusLabel();
                    StartStatsTimer();
                });
            }
            catch (Exception ex)
            {
                var msg = ex.Message;
                try
                {
                    BeginInvoke(() =>
                    {
                        _btnConnect.Text = "Connect";
                        _btnConnect.Enabled = true;
                        UpdateStatusLabel();
                        MessageBox.Show("Failed to connect:\n" + msg, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                    });
                }
                catch { }
            }
        });
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

    // ──────────────────────────────────────────────
    //  Settings & subscriptions
    // ──────────────────────────────────────────────

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
            if (IsDisposed) return;
            if (InvokeRequired) { try { BeginInvoke(UpdateStatusLabel); } catch { } return; }
            UpdateStatusLabel();
        };
    }

    private void UpdateStatusLabel()
    {
        var connected = _trayContext.RedisMonitor.IsConnected;
        if (connected)
        {
            _lblStatus.Text = "\u25cf Connected";
            _lblStatus.ForeColor = AccentGreen;
            _btnConnect.Text = "Reconnect";
            StartStatsTimer();
        }
        else
        {
            _lblStatus.Text = "\u25cf Disconnected (auto-reconnecting\u2026)";
            _lblStatus.ForeColor = AccentRed;
        }
        _btnAddWorker.Enabled = connected;
    }

    private void SaveCurrentWorkerCount()
    {
        _settings.WorkerCount = _trayContext.ProcessManager.CurrentCount;
        try { _settings.Save(); } catch { }
    }

    // ──────────────────────────────────────────────
    //  Timers
    // ──────────────────────────────────────────────

    private void StartStatsTimer()
    {
        if (_statsTimer != null) return;
        _statsTimer = new System.Windows.Forms.Timer { Interval = 3000 };
        _statsTimer.Tick += (_, _) => RefreshQueueStats();
        _statsTimer.Start();
        RefreshQueueStats();
    }

    private void StartDurationTimer()
    {
        _durationTimer = new System.Windows.Forms.Timer { Interval = 1000 };
        _durationTimer.Tick += (_, _) =>
        {
            foreach (var card in _workerCards.Values)
                card.UpdateDuration();
            foreach (var card in _adoptedCards.Values)
                card.UpdateDuration();
        };
        _durationTimer.Start();
    }

    private volatile bool _refreshingStats;

    private void RefreshQueueStats()
    {
        var conn = _trayContext.RedisMonitor.Connection;
        if (conn == null || !conn.IsConnected || _refreshingStats) return;
        _refreshingStats = true;

        var queueNames = (_settings.QueueNames ?? AppSettings.DefaultQueueNames)
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

        Task.Run(() =>
        {
            try
            {
                var db = conn.GetDatabase();
                var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                long totalQueued = 0, totalActive = 0, totalFailed = 0, totalDone = 0;
                var name = "";
                foreach (var q in queueNames)
                {
                    name = q;
                    totalQueued += db.ListLength($"rq:queue:{q}");
                    totalActive += db.SortedSetLength($"rq:wip:{q}", now, double.PositiveInfinity);
                    totalFailed += db.SortedSetLength($"rq:failed:{q}");
                    totalDone += db.SortedSetLength($"rq:finished:{q}");
                }
                var n = name; var tq = totalQueued; var ta = totalActive; var tf = totalFailed; var td = totalDone;
                try { BeginInvoke(() => _queueStats.Update(n, tq, ta, tf, td)); } catch { }
            }
            catch { }
            finally { _refreshingStats = false; }
        });
    }

    // ──────────────────────────────────────────────
    //  Lifecycle
    // ──────────────────────────────────────────────

    protected override void OnShown(EventArgs e)
    {
        base.OnShown(e);
        foreach (var line in AppLog.GetBuffer())
            AppendLog(line);

        try
        {
            _splitMain.Panel1MinSize = 280;
            _splitMain.Panel2MinSize = 240;
            _splitMain.SplitterDistance = Math.Max(280, _splitMain.Width - 380);
        }
        catch { }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _statsTimer?.Stop();
            _statsTimer?.Dispose();
            _durationTimer?.Stop();
            _durationTimer?.Dispose();
        }
        base.Dispose(disposing);
    }

    // ══════════════════════════════════════════════
    //  QueueStatsBar — inline colored stat display
    // ══════════════════════════════════════════════

    private sealed class QueueStatsBar : Panel
    {
        private static readonly Font NameFont = new("Segoe UI Semibold", 8.5f);
        private static readonly Font NumFont = new("Segoe UI Semibold", 8.5f);
        private static readonly Font LabelFont = new("Segoe UI", 7.5f);

        private string _queue = "";
        private long _queued, _active, _failed, _done;

        public QueueStatsBar()
        {
            BackColor = Color.Transparent;
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true);
        }

        public void Update(string queue, long queued, long active, long failed, long done)
        {
            _queue = queue; _queued = queued; _active = active; _failed = failed; _done = done;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;

            float x = 0;
            using var nameBrush = new SolidBrush(Color.FromArgb(0x33, 0x41, 0x55));
            g.DrawString(_queue, NameFont, nameBrush, x, 2);
            x += g.MeasureString(_queue, NameFont).Width + 12;

            DrawStat(g, ref x, _queued.ToString(), "queued", Color.FromArgb(0x64, 0x74, 0x8B));
            DrawStat(g, ref x, _active.ToString(), "active", AccentOrange);
            DrawStat(g, ref x, _failed.ToString(), "failed", AccentRed);
            DrawStat(g, ref x, _done.ToString(), "done", AccentGreen);
        }

        private void DrawStat(Graphics g, ref float x, string num, string label, Color color)
        {
            using var numBrush = new SolidBrush(color);
            using var lblBrush = new SolidBrush(Muted);
            g.DrawString(num, NumFont, numBrush, x, 2);
            x += g.MeasureString(num, NumFont).Width - 2;
            g.DrawString(label, LabelFont, lblBrush, x, 4);
            x += g.MeasureString(label, LabelFont).Width + 8;
        }
    }

    // ══════════════════════════════════════════════
    //  WorkerCard — custom-painted card
    // ══════════════════════════════════════════════

    private sealed class WorkerCard : Panel
    {
        private const int CardW = 190;
        private const int CardH = 175;
        private const int Radius = 10;
        private const int DotSize = 14;

        private static readonly Font NameFont = new("Segoe UI Semibold", 9.5f);
        private static readonly Font StateFont = new("Segoe UI", 8.5f);
        private static readonly Font JobFont = new("Segoe UI", 7.5f);
        private static readonly Font TimeFont = new("Consolas", 14f, FontStyle.Bold);
        private static readonly Font StopFont = new("Segoe UI", 14f);

        private static readonly Rectangle StopBtnRect = new(CardW - 32, 4, 26, 26);

        private string _state = "idle";
        private string _displayState = "idle";
        private Color _stateColor = AccentGreen;
        private string? _jobId;
        private string? _modelPath;
        private DateTime? _busySince;
        private bool _isSelected;
        private bool _isHovering;
        private string _displayTime = "--:--";

        public string WorkerName { get; }

        public bool IsSelected
        {
            get => _isSelected;
            set { _isSelected = value; Invalidate(); }
        }

        public event Action<WorkerCard>? StopRequested;
        public event Action<WorkerCard>? Clicked;

        public WorkerCard(string name)
        {
            WorkerName = name;
            Size = new Size(CardW, CardH);
            Margin = new Padding(6);
            Cursor = Cursors.Hand;
            BackColor = BgColor;

            SetStyle(
                ControlStyles.AllPaintingInWmPaint |
                ControlStyles.UserPaint |
                ControlStyles.OptimizedDoubleBuffer |
                ControlStyles.ResizeRedraw,
                true);

            UpdateRegion();
        }

        public void UpdateState(string state, string? jobId, string? modelPath = null)
        {
            _state = state;
            _jobId = jobId;
            _modelPath = modelPath;

            if (state == "busy" && _busySince == null)
                _busySince = DateTime.UtcNow;
            else if (state != "busy" && !state.StartsWith("start-delay"))
                _busySince = null;

            var (ds, color, countdown) = ParseState(state);
            _displayState = ds;
            _stateColor = color;

            if (countdown != null)
                _displayTime = countdown;
            else if (state == "idle")
                _displayTime = "--:--";

            Invalidate();
        }

        public void UpdateDuration()
        {
            if (_busySince.HasValue)
            {
                var elapsed = DateTime.UtcNow - _busySince.Value;
                var t = elapsed.TotalHours >= 1
                    ? $"{(int)elapsed.TotalHours}:{elapsed.Minutes:D2}:{elapsed.Seconds:D2}"
                    : $"{elapsed.Minutes:D2}:{elapsed.Seconds:D2}";
                if (t != _displayTime) { _displayTime = t; Invalidate(); }
            }
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            if (!_isHovering) { _isHovering = true; Invalidate(); }
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            base.OnMouseLeave(e);
            if (_isHovering) { _isHovering = false; Invalidate(); }
        }

        protected override void OnMouseClick(MouseEventArgs e)
        {
            base.OnMouseClick(e);
            if (e.Button != MouseButtons.Left) return;
            if (_isHovering && StopBtnRect.Contains(e.Location))
                StopRequested?.Invoke(this);
            else
                Clicked?.Invoke(this);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;

            var rect = new Rectangle(0, 0, Width - 1, Height - 1);
            using var bgPath = RoundedRect(rect, Radius);

            if (_isHovering && !_isSelected)
            {
                using var shadow = new SolidBrush(Color.FromArgb(8, 0, 0, 0));
                g.FillPath(shadow, RoundedRect(new Rectangle(1, 2, Width - 1, Height - 1), Radius));
            }

            g.FillPath(Brushes.White, bgPath);

            var borderColor = _isSelected ? SelectedBorder
                : _isHovering ? Color.FromArgb(0xBD, 0xC5, 0xD0)
                : CardBorder;
            using var borderPen = new Pen(borderColor, _isSelected ? 2f : 1f);
            g.DrawPath(borderPen, bgPath);

            var nameText = WorkerName.Length > 16 ? WorkerName[..13] + "\u2026" : WorkerName;
            using var nameBrush = new SolidBrush(Color.FromArgb(0x1E, 0x29, 0x3B));
            g.DrawString(nameText, NameFont, nameBrush, 14, 14);

            if (_isHovering)
            {
                var mp = PointToClient(Control.MousePosition);
                var stopColor = StopBtnRect.Contains(mp) ? AccentRed : Muted;
                using var stopBrush = new SolidBrush(stopColor);
                g.DrawString("\u00d7", StopFont, stopBrush, StopBtnRect.X, StopBtnRect.Y);
            }

            var dotX = (Width - DotSize) / 2;
            var dotY = 48;

            if (_state == "busy")
            {
                using var glow = new SolidBrush(Color.FromArgb(25, _stateColor));
                g.FillEllipse(glow, dotX - 8, dotY - 8, DotSize + 16, DotSize + 16);
            }

            using var dotBrush = new SolidBrush(_stateColor);
            g.FillEllipse(dotBrush, dotX, dotY, DotSize, DotSize);

            using var stateBrush = new SolidBrush(_stateColor);
            var stateSize = g.MeasureString(_displayState, StateFont);
            g.DrawString(_displayState, StateFont, stateBrush,
                (Width - stateSize.Width) / 2, 68);

            if (!string.IsNullOrEmpty(_jobId))
            {
                var jobText = _jobId.Length > 22 ? _jobId[..19] + "\u2026" : _jobId;
                using var jobBrush = new SolidBrush(Muted);
                var jobSize = g.MeasureString(jobText, JobFont);
                g.DrawString(jobText, JobFont, jobBrush,
                    (Width - jobSize.Width) / 2, 90);

                if (!string.IsNullOrEmpty(_modelPath))
                {
                    var fileName = System.IO.Path.GetFileNameWithoutExtension(_modelPath);
                    var fileText = fileName.Length > 22 ? fileName[..19] + "\u2026" : fileName;
                    var fileSize = g.MeasureString(fileText, JobFont);
                    using var fileBrush = new SolidBrush(Color.FromArgb(0x47, 0x55, 0x69));
                    g.DrawString(fileText, JobFont, fileBrush,
                        (Width - fileSize.Width) / 2, 103);
                }
            }

            var timeColor = _state == "busy" ? AccentOrange
                : _state.StartsWith("start-delay") ? AccentAmber
                : Muted;
            using var timeBrush = new SolidBrush(timeColor);
            var timeSize = g.MeasureString(_displayTime, TimeFont);
            g.DrawString(_displayTime, TimeFont, timeBrush,
                (Width - timeSize.Width) / 2, 120);
        }

        private void UpdateRegion()
        {
            using var path = RoundedRect(new Rectangle(0, 0, Width, Height), Radius);
            Region?.Dispose();
            Region = new Region(path);
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            UpdateRegion();
        }

        private static (string displayState, Color color, string? countdown) ParseState(string state)
        {
            if (state == "idle") return ("idle", AccentGreen, null);
            if (state == "busy") return ("busy", AccentOrange, null);
            if (state == "stopped") return ("stopped", Color.Gray, null);
            if (state.StartsWith("start-delay"))
            {
                var m = Regex.Match(state, @"\((\d+)s\)");
                return ("waiting", AccentAmber, m.Success ? m.Groups[1].Value + "s" : "\u2026");
            }
            return (state, Color.Gray, null);
        }

        private static GraphicsPath RoundedRect(Rectangle bounds, int radius)
        {
            var path = new GraphicsPath();
            var d = radius * 2;
            path.AddArc(bounds.X, bounds.Y, d, d, 180, 90);
            path.AddArc(bounds.Right - d, bounds.Y, d, d, 270, 90);
            path.AddArc(bounds.Right - d, bounds.Bottom - d, d, d, 0, 90);
            path.AddArc(bounds.X, bounds.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }
    }

    // ══════════════════════════════════════════════
    //  AdoptedJobCard — inherited/handoff job monitor
    // ══════════════════════════════════════════════

    private sealed class AdoptedJobCard : Panel
    {
        private const int CardW = 190;
        private const int CardH = 175;
        private const int Radius = 10;
        private const int DotSize = 14;

        private static readonly Font BadgeFont  = new("Segoe UI", 7f, FontStyle.Bold);
        private static readonly Font NameFont   = new("Segoe UI Semibold", 9f);
        private static readonly Font StateFont  = new("Segoe UI", 8.5f);
        private static readonly Font PidFont    = new("Segoe UI", 7.5f);
        private static readonly Font TimeFont   = new("Consolas", 14f, FontStyle.Bold);

        private static readonly Color CardAmber  = Color.FromArgb(0xD9, 0x77, 0x06);
        private static readonly Color BadgeBg    = Color.FromArgb(0xFF, 0xF7, 0xED);
        private static readonly Color BadgeFg    = Color.FromArgb(0xD9, 0x77, 0x06);

        private readonly string _jobId;
        private readonly string _workerName;
        private readonly int _pid;
        private readonly DateTime _adoptedAt;
        private readonly string? _modelPath;
        private string _displayTime = "00:00";
        private bool _done;
        private bool _doneSuccess;

        public AdoptedJobCard(string jobId, string workerName, int pid, DateTime adoptedAt, string? modelPath = null)
        {
            _jobId = jobId;
            _workerName = workerName;
            _pid = pid;
            _adoptedAt = adoptedAt;
            _modelPath = modelPath;

            Size = new Size(CardW, CardH);
            Margin = new Padding(6);
            BackColor = BgColor;

            SetStyle(
                ControlStyles.AllPaintingInWmPaint |
                ControlStyles.UserPaint |
                ControlStyles.OptimizedDoubleBuffer |
                ControlStyles.ResizeRedraw,
                true);

            UpdateRegion();
        }

        public void UpdateDuration()
        {
            if (_done) return;
            var elapsed = DateTime.UtcNow - _adoptedAt;
            var t = elapsed.TotalHours >= 1
                ? $"{(int)elapsed.TotalHours}:{elapsed.Minutes:D2}:{elapsed.Seconds:D2}"
                : $"{elapsed.Minutes:D2}:{elapsed.Seconds:D2}";
            if (t != _displayTime) { _displayTime = t; Invalidate(); }
        }

        public void MarkDone(bool success)
        {
            _done = true;
            _doneSuccess = success;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;

            var rect = new Rectangle(0, 0, Width - 1, Height - 1);
            using var bgPath = RoundedRect(rect, Radius);

            // White fill
            using var bgBrush = new SolidBrush(Color.White);
            g.FillPath(bgBrush, bgPath);

            // Amber border (2px)
            var borderColor = _done
                ? (_doneSuccess ? AccentGreen : AccentRed)
                : CardAmber;
            using var borderPen = new Pen(borderColor, 2f);
            g.DrawPath(borderPen, bgPath);

            // "INHERITED" badge at top
            var badgeText = "INHERITED";
            var badgeSize = g.MeasureString(badgeText, BadgeFont);
            var badgeRect = new RectangleF((Width - badgeSize.Width - 10) / 2, 8, badgeSize.Width + 10, badgeSize.Height + 4);
            using var badgeBgBrush = new SolidBrush(BadgeBg);
            using var badgePath = RoundedRect(Rectangle.Round(badgeRect), 3);
            g.FillPath(badgeBgBrush, badgePath);
            using var badgeFgBrush = new SolidBrush(BadgeFg);
            g.DrawString(badgeText, BadgeFont, badgeFgBrush, badgeRect.X + 5, badgeRect.Y + 2);

            // Worker name
            var shortName = _workerName.Length > 20 ? _workerName[..17] + "\u2026" : _workerName;
            var nameSize = g.MeasureString(shortName, NameFont);
            using var nameBrush = new SolidBrush(Color.FromArgb(0x1E, 0x29, 0x3B));
            g.DrawString(shortName, NameFont, nameBrush, (Width - nameSize.Width) / 2, 30);

            // State dot
            var dotX = (Width - DotSize) / 2;
            var dotY = 52;
            var dotColor = _done ? (_doneSuccess ? AccentGreen : AccentRed) : CardAmber;
            if (!_done)
            {
                using var glow = new SolidBrush(Color.FromArgb(25, dotColor));
                g.FillEllipse(glow, dotX - 8, dotY - 8, DotSize + 16, DotSize + 16);
            }
            using var dotBrush = new SolidBrush(dotColor);
            g.FillEllipse(dotBrush, dotX, dotY, DotSize, DotSize);

            // State label
            var stateText = _done ? (_doneSuccess ? "finished" : "failed") : "monitoring";
            using var stateBrush = new SolidBrush(dotColor);
            var stateSize = g.MeasureString(stateText, StateFont);
            g.DrawString(stateText, StateFont, stateBrush, (Width - stateSize.Width) / 2, 72);

            // PID
            var pidText = $"PID {_pid}";
            using var pidBrush = new SolidBrush(Muted);
            var pidSize = g.MeasureString(pidText, PidFont);
            g.DrawString(pidText, PidFont, pidBrush, (Width - pidSize.Width) / 2, 90);

            // Job ID (truncated)
            var jobText = _jobId.Length > 22 ? _jobId[..19] + "\u2026" : _jobId;
            var jobSize = g.MeasureString(jobText, PidFont);
            g.DrawString(jobText, PidFont, pidBrush, (Width - jobSize.Width) / 2, 102);

            // Model filename
            if (!string.IsNullOrEmpty(_modelPath))
            {
                var fileName = System.IO.Path.GetFileNameWithoutExtension(_modelPath);
                var fileText = fileName.Length > 22 ? fileName[..19] + "\u2026" : fileName;
                var fileSize = g.MeasureString(fileText, PidFont);
                using var fileBrush = new SolidBrush(Color.FromArgb(0x47, 0x55, 0x69));
                g.DrawString(fileText, PidFont, fileBrush, (Width - fileSize.Width) / 2, 114);
            }

            // Timer
            var timeColor = _done ? Muted : CardAmber;
            using var timeBrush = new SolidBrush(timeColor);
            var timeStr = _done ? (_doneSuccess ? "done" : "failed") : _displayTime;
            var timeSize = g.MeasureString(timeStr, TimeFont);
            g.DrawString(timeStr, TimeFont, timeBrush, (Width - timeSize.Width) / 2, 128);
        }

        private void UpdateRegion()
        {
            using var path = RoundedRect(new Rectangle(0, 0, Width, Height), Radius);
            Region?.Dispose();
            Region = new Region(path);
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            UpdateRegion();
        }

        private static GraphicsPath RoundedRect(Rectangle bounds, int radius)
        {
            var path = new GraphicsPath();
            var d = radius * 2;
            path.AddArc(bounds.X, bounds.Y, d, d, 180, 90);
            path.AddArc(bounds.Right - d, bounds.Y, d, d, 270, 90);
            path.AddArc(bounds.Right - d, bounds.Bottom - d, d, d, 0, 90);
            path.AddArc(bounds.X, bounds.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }
    }
}
