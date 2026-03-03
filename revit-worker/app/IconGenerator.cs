using System.Drawing;
using System.Drawing.Drawing2D;

namespace RevitWorkerApp;

public static class IconGenerator
{
    public enum TrayState
    {
        Red,    // disconnected / paused
        Green,  // connected, idle
        Orange  // working
    }

    private static readonly Color GreenFill = Color.FromArgb(0x22, 0xc5, 0x5e);
    private static readonly Color OrangeFill = Color.FromArgb(0xf5, 0x9e, 0x0b);
    private static readonly Color RedFill = Color.FromArgb(0xef, 0x44, 0x44);
    private static readonly Color BorderColor = Color.FromArgb(0x1f, 0x29, 0x33);

    public static Icon CreateIcon(TrayState state, int size = 32)
    {
        using var bmp = CreateBitmap(state, size);
        var hIcon = bmp.GetHicon();
        try
        {
            return Icon.FromHandle(hIcon).Clone() as Icon ?? CreateFallback(state, size);
        }
        finally
        {
            _ = Interop.DestroyIcon(hIcon);
        }
    }

    private static Bitmap CreateBitmap(TrayState state, int size)
    {
        var bmp = new Bitmap(size, size);
        using var g = Graphics.FromImage(bmp);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(Color.Transparent);

        var fill = state switch
        {
            TrayState.Green => GreenFill,
            TrayState.Orange => OrangeFill,
            _ => RedFill
        };

        var margin = Math.Max(1, size / 16);
        var rect = new Rectangle(margin, margin, size - 2 * margin, size - 2 * margin);

        using (var brush = new SolidBrush(fill))
            g.FillEllipse(brush, rect);
        using (var pen = new Pen(BorderColor, Math.Max(1, size / 16)))
            g.DrawEllipse(pen, rect);

        return bmp;
    }

    private static Icon CreateFallback(TrayState state, int size)
    {
        using var bmp = CreateBitmap(state, size);
        return Icon.FromHandle(bmp.GetHicon());
    }

    public static Icon CreateRed() => CreateIcon(TrayState.Red);
    public static Icon CreateGreen() => CreateIcon(TrayState.Green);
    public static Icon CreateOrange() => CreateIcon(TrayState.Orange);

    private static class Interop
    {
        [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Auto)]
        internal static extern bool DestroyIcon(IntPtr handle);
    }
}
