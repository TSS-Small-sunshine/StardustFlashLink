<#
    generate-wizard-images.ps1 —— 生成 Inno Setup 向导品牌图（零依赖，只用 .NET GDI+）

    为什么要有这个脚本，而不是直接提交几张图：
      1) 旧图把版本号写进了位图（branding\wizard.bmp 里是 "v2.0.0"，发到 v2.0.4.0 时已过期）。
         本脚本生成的图**不含任何版本号**——版本由向导自己显示，永远不会过期。
      2) DPI 变体的像素尺寸是硬要求（见 Inno 文档 WizardImageFile / WizardSmallImageFile），
         手改容易漏、容易错，脚本按权威尺寸表批量产出。
      3) 设计可复现、可微调：改颜色/字号再跑一次即可。

    产出（写入本脚本同目录）：
      左侧竖图 202x386 / 269x515 / 336x643 / 403x772 / 430x824 （100%~200% DPI，宽高比 164:314）
      右上角方图 58x58 / 77x77 / 97x97 / 116x116 / 124x124

    用法：  powershell -ExecutionPolicy Bypass -File packaging\branding\generate-wizard-images.ps1
#>

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$Here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogoPng = Join-Path $Here 'web-logo-256.png'

# ── 品牌令牌（与 Web UI 对齐：--accent #0071e3 / 暗色 #0a84ff）────────────────
$BrandBlue  = [System.Drawing.Color]::FromArgb(255,  42, 168, 255)   # #2AA8FF
$BrandMint  = [System.Drawing.Color]::FromArgb(255,  61, 220, 151)   # #3DDC97
$PanelTop   = [System.Drawing.Color]::FromArgb(255,  10,  15,  26)   # #0A0F1A
$PanelBot   = [System.Drawing.Color]::FromArgb(255,   5,   8,  14)   # #05080E
$TextStrong = [System.Drawing.Color]::White
$TextLatin  = [System.Drawing.Color]::FromArgb(255, 143, 160, 184)   # #8FA0B8
$TextTag    = [System.Drawing.Color]::FromArgb(255, 154, 167, 184)   # #9AA7B8

# 权威尺寸表（Inno Setup 文档：左侧图 100%…250%，右上小图同）
$LargeSizes = @( @(202,386), @(269,515), @(336,643), @(403,772), @(430,824) )
$SmallSizes = @( @(58,58), @(77,77), @(97,97), @(116,116), @(124,124) )

# ── 辅助：圆角矩形路径 ────────────────────────────────────────────────────────
function New-RoundedRectPath([float]$x, [float]$y, [float]$w, [float]$h, [float]$r) {
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = $r * 2
    if ($d -gt $w) { $d = $w }
    if ($d -gt $h) { $d = $h }
    $p.AddArc($x,           $y,           $d, $d, 180, 90)
    $p.AddArc($x + $w - $d, $y,           $d, $d, 270, 90)
    $p.AddArc($x + $w - $d, $y + $h - $d, $d, $d,   0, 90)
    $p.AddArc($x,           $y + $h - $d, $d, $d,  90, 90)
    $p.CloseFigure()
    return $p
}

# ── 辅助：四角星（"星尘"意象），中心点 + 半径 + 收腰系数 ──────────────────────
function New-SparklePath([double]$cx, [double]$cy, [double]$r, [double]$waist) {
    $p  = New-Object System.Drawing.Drawing2D.GraphicsPath
    $iw = $r * $waist
    $pts = New-Object 'System.Drawing.PointF[]' 8
    $pts[0] = [System.Drawing.PointF]::new([float]$cx,          [float]($cy - $r))
    $pts[1] = [System.Drawing.PointF]::new([float]($cx + $iw),  [float]($cy - $iw))
    $pts[2] = [System.Drawing.PointF]::new([float]($cx + $r),   [float]$cy)
    $pts[3] = [System.Drawing.PointF]::new([float]($cx + $iw),  [float]($cy + $iw))
    $pts[4] = [System.Drawing.PointF]::new([float]$cx,          [float]($cy + $r))
    $pts[5] = [System.Drawing.PointF]::new([float]($cx - $iw),  [float]($cy + $iw))
    $pts[6] = [System.Drawing.PointF]::new([float]($cx - $r),   [float]$cy)
    $pts[7] = [System.Drawing.PointF]::new([float]($cx - $iw),  [float]($cy - $iw))
    $p.AddPolygon($pts)
    return $p
}

# ── 辅助：带字距的文本（GDI+ 无 letter-spacing，逐字排布）────────────────────
function Draw-TrackedText($g, [string]$text, $font, $brush, [float]$cx, [float]$y, [float]$tracking) {
    $widths = @()
    $total = 0.0
    foreach ($ch in $text.ToCharArray()) {
        $s = [string]$ch
        $w = $g.MeasureString($s, $font).Width
        $widths += $w
        $total += $w + $tracking
    }
    $total -= $tracking
    $x = $cx - $total / 2
    for ($i = 0; $i -lt $text.Length; $i++) {
        $s = [string]$text[$i]
        $g.DrawString($s, $font, $brush, $x, $y)
        $x += $widths[$i] + $tracking
    }
    return $total
}

# ── 读取 LOGO 并裁掉四周白边（源图 256x256 白底；白底落在白卡片上天然无缝）──
if (-not (Test-Path $LogoPng)) { throw "找不到 LOGO 源图：$LogoPng" }
$Logo = [System.Drawing.Bitmap]::FromFile($LogoPng)
$minX = $Logo.Width; $minY = $Logo.Height; $maxX = -1; $maxY = -1
for ($y = 0; $y -lt $Logo.Height; $y++) {
    for ($x = 0; $x -lt $Logo.Width; $x++) {
        $c = $Logo.GetPixel($x, $y)
        if ($c.A -gt 16 -and ($c.R -lt 245 -or $c.G -lt 245 -or $c.B -lt 245)) {
            if ($x -lt $minX) { $minX = $x }
            if ($x -gt $maxX) { $maxX = $x }
            if ($y -lt $minY) { $minY = $y }
            if ($y -gt $maxY) { $maxY = $y }
        }
    }
}
if ($maxX -lt 0) { throw 'LOGO 里没有非白内容，源图可能损坏' }
$LogoSrc = New-Object System.Drawing.Rectangle($minX, $minY, ($maxX - $minX + 1), ($maxY - $minY + 1))
Write-Host ("LOGO 内容区: {0}x{1} @({2},{3})" -f $LogoSrc.Width, $LogoSrc.Height, $LogoSrc.X, $LogoSrc.Y)

# ── 左侧竖图（欢迎页 / 完成页显示）────────────────────────────────────────────
function New-LargePanel([int]$w, [int]$h) {
    $s = $w / 202.0                       # 以 100% DPI 尺寸为基准的比例因子
    $bmp = New-Object System.Drawing.Bitmap($w, $h, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g   = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode     = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode   = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAlias

    # 1) 底色：近黑深蓝，自上而下渐暗
    $bgRect  = New-Object System.Drawing.Rectangle(0, 0, $w, $h)
    $bgBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($bgRect, $PanelTop, $PanelBot, 90.0)
    $g.FillRectangle($bgBrush, $bgRect)
    $bgBrush.Dispose()

    # 2) 品牌光晕：蓝→薄荷 柔和辉光，压在 LOGO 卡后面
    $glowD    = $w * 1.15
    $glowRect = New-Object System.Drawing.RectangleF((($w - $glowD) / 2), ($h * 0.10), $glowD, $glowD)
    $glowPath = New-Object System.Drawing.Drawing2D.GraphicsPath
    $glowPath.AddEllipse($glowRect)
    $glowBrush = New-Object System.Drawing.Drawing2D.PathGradientBrush($glowPath)
    $glowBrush.CenterColor     = [System.Drawing.Color]::FromArgb(72, $BrandBlue)
    $glowBrush.SurroundColors  = @([System.Drawing.Color]::FromArgb(0, $BrandMint))
    $g.FillPath($glowBrush, $glowPath)
    $glowBrush.Dispose(); $glowPath.Dispose()

    # 3) 星尘点阵（固定随机种子 → 每次生成完全一致，可复现）
    $rnd = New-Object System.Random(20260927)
    $dotCount = [int](26 * $s)
    for ($i = 0; $i -lt $dotCount; $i++) {
        $dx = $rnd.NextDouble() * $w
        $dy = $rnd.NextDouble() * ($h * 0.72)
        $rr = (0.5 + $rnd.NextDouble() * 0.9) * $s
        $al = 26 + $rnd.Next(0, 92)
        $cc = if ($rnd.Next(0, 3) -eq 0) { $BrandMint } else { [System.Drawing.Color]::White }
        $dot = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb($al, $cc))
        $g.FillEllipse($dot, [float]($dx - $rr), [float]($dy - $rr), [float]($rr * 2), [float]($rr * 2))
        $dot.Dispose()
    }
    # 三颗四角星，呼应"星尘"
    foreach ($sp in @(@(0.24, 0.09, 5.2, 0.30), @(0.78, 0.16, 3.6, 0.28), @(0.16, 0.30, 2.8, 0.26))) {
        $sPath = New-SparklePath ($sp[0] * $w) ($sp[1] * $h) ($sp[2] * $s) $sp[3]
        $sBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(150, $BrandMint))
        $g.FillPath($sBrush, $sPath)
        $sBrush.Dispose(); $sPath.Dispose()
    }

    # 4) LOGO 白卡（圆角）+ 卡内 LOGO
    $card   = 96 * $s
    $cardX  = ($w - $card) / 2
    $blockH = $card + (26 + 30 + 20 + 16 + 22) * $s
    $cardY  = [Math]::Max((22 * $s), (($h - $blockH) / 2 - 16 * $s))
    $cardPath = New-RoundedRectPath $cardX $cardY $card $card (22 * $s)
    $cardBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $g.FillPath($cardBrush, $cardPath)
    $cardBrush.Dispose(); $cardPath.Dispose()

    $pad = 15 * $s
    $destRect = New-Object System.Drawing.Rectangle(
        [int][Math]::Round($cardX + $pad), [int][Math]::Round($cardY + $pad),
        [int][Math]::Round($card - $pad * 2), [int][Math]::Round($card - $pad * 2))
    $g.DrawImage($Logo, $destRect, $LogoSrc.X, $LogoSrc.Y, $LogoSrc.Width, $LogoSrc.Height, [System.Drawing.GraphicsUnit]::Pixel)

    # 5) 文案块：中文名 / 拉丁名 / 品牌色分隔线 / 副标题
    $cx    = $w / 2.0
    $yWord = $cardY + $card + 26 * $s
    $fWord = New-Object System.Drawing.Font('Microsoft YaHei UI', (21 * $s), [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $bWord = New-Object System.Drawing.SolidBrush($TextStrong)
    [void](Draw-TrackedText $g '星尘闪连' $fWord $bWord $cx $yWord (1.6 * $s))
    $fWord.Dispose(); $bWord.Dispose()

    $yLatin = $yWord + 30 * $s
    $fLatin = New-Object System.Drawing.Font('Segoe UI', (7.6 * $s), [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
    $bLatin = New-Object System.Drawing.SolidBrush($TextLatin)
    [void](Draw-TrackedText $g 'STARDUST FLASH LINK' $fLatin $bLatin $cx $yLatin (2.2 * $s))
    $fLatin.Dispose(); $bLatin.Dispose()

    $yLine   = $yLatin + 20 * $s
    $lineW   = 58 * $s
    $lineRect = New-Object System.Drawing.RectangleF(($cx - $lineW / 2), $yLine, $lineW, [float](1.2 * $s))
    $lineBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($lineRect,
        [System.Drawing.Color]::FromArgb(0, $BrandBlue), [System.Drawing.Color]::FromArgb(0, $BrandMint), 0.0)
    $blend = New-Object System.Drawing.Drawing2D.ColorBlend(3)
    $blend.Colors = @(
        [System.Drawing.Color]::FromArgb(0,   $BrandBlue),
        [System.Drawing.Color]::FromArgb(215, $BrandMint),
        [System.Drawing.Color]::FromArgb(0,   $BrandMint))
    $blend.Positions = @(0.0, 0.5, 1.0)
    $lineBrush.InterpolationColors = $blend
    $g.FillRectangle($lineBrush, $lineRect)
    $lineBrush.Dispose()

    $yTag = $yLine + 16 * $s
    $fTag = New-Object System.Drawing.Font('Microsoft YaHei UI', (10.5 * $s), [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
    $bTag = New-Object System.Drawing.SolidBrush($TextTag)
    [void](Draw-TrackedText $g 'Dr.COM 校园网自动登录' $fTag $bTag $cx $yTag (0.4 * $s))
    $fTag.Dispose(); $bTag.Dispose()

    $g.Dispose()
    return $bmp
}

# ── 右上角方图（向导顶部右侧；透明底，融入暗色面板）──────────────────────────
function New-SmallCard([int]$w, [int]$h) {
    $bmp = New-Object System.Drawing.Bitmap($w, $h, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g   = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode     = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode   = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)

    $path  = New-RoundedRectPath 0 0 $w $h ($w * 0.24)
    $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $g.FillPath($brush, $path)
    $brush.Dispose(); $path.Dispose()

    $pad = $w * 0.15
    $dr  = New-Object System.Drawing.Rectangle([int][Math]::Round($pad), [int][Math]::Round($pad), [int][Math]::Round($w - $pad * 2), [int][Math]::Round($h - $pad * 2))
    $g.DrawImage($Logo, $dr, $LogoSrc.X, $LogoSrc.Y, $LogoSrc.Width, $LogoSrc.Height, [System.Drawing.GraphicsUnit]::Pixel)
    $g.Dispose()
    return $bmp
}

# ── 生成并落盘 ───────────────────────────────────────────────────────────────
$made = @()
foreach ($sz in $LargeSizes) {
    $w = [int]$sz[0]; $h = [int]$sz[1]
    $bmp  = New-LargePanel $w $h
    $name = "wizard-left-$w`x$h.png"
    $bmp.Save((Join-Path $Here $name), [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    $made += $name
}
foreach ($sz in $SmallSizes) {
    $w = [int]$sz[0]; $h = [int]$sz[1]
    $bmp  = New-SmallCard $w $h
    $name = "wizard-small-$w`x$h.png"
    $bmp.Save((Join-Path $Here $name), [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    $made += $name
}
$Logo.Dispose()

Write-Host ''
Write-Host ('已生成 {0} 个文件 → {1}' -f $made.Count, $Here)
foreach ($n in $made) { Write-Host ('  ' + $n) }
Write-Host ''
Write-Host '提示：图上不含版本号（版本由向导自身显示），换版本无需重新生成。'

