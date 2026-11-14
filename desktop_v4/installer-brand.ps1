# Native installer illustrations, generated reproducibly on Windows.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
function New-BrandBitmap([string]$Path,[int]$W,[int]$H,[bool]$Large) {
  $bmp = [System.Drawing.Bitmap]::new($W,$H,[System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
  $bg = [System.Drawing.Color]::FromArgb(20,25,32)
  $gold = [System.Drawing.Color]::FromArgb(245,189,83)
  $g.Clear($bg)
  $fg = [System.Drawing.SolidBrush]::new($gold)
  $white = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(235,240,245))
  $muted = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(158,170,184))
  $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(69,59,43),1)
  try {
    if ($Large) {
      for($x=0;$x -lt $W;$x+=22) { $g.DrawLine($pen,$x,0,$x,$H) }
      for($n=0;$n -lt $H;$n+=22) { $g.DrawLine($pen,0,$n,$W,$n) }
      $g.DrawEllipse($pen,-35,89,236,236); $g.DrawEllipse($pen,-10,114,186,186)
      $g.FillRectangle($fg,19,24,36,27)
      $points = @([System.Drawing.Point]::new(32,30),[System.Drawing.Point]::new(32,45),[System.Drawing.Point]::new(45,37))
      $dark=[System.Drawing.SolidBrush]::new($bg)
      $g.FillPolygon($dark,[System.Drawing.Point[]]$points);$dark.Dispose()
      $font=[System.Drawing.Font]::new('Segoe UI',13,[System.Drawing.FontStyle]::Bold)
      $tiny=[System.Drawing.Font]::new('Segoe UI',8)
      $largeNum=[System.Drawing.Font]::new('Segoe UI',52,[System.Drawing.FontStyle]::Bold)
      $g.DrawString('LinkVideo',$font,$white,18,62)
      $g.DrawString('HELPER / WORKSPACE',$tiny,$muted,18,86)
      $g.DrawString('04',$largeNum,$fg,16,160)
      $g.DrawString('DESKTOP / WINDOWS',$tiny,$white,18,270)
      $g.DrawString('PREVIEW BUILD',$tiny,$muted,18,286)
      $font.Dispose();$tiny.Dispose();$largeNum.Dispose()
    } else {
      $g.FillRectangle($fg,7,12,41,31)
      $dark=[System.Drawing.SolidBrush]::new($bg)
      $g.FillPolygon($dark,[System.Drawing.Point[]]@([System.Drawing.Point]::new(22,18),[System.Drawing.Point]::new(22,36),[System.Drawing.Point]::new(37,27)));$dark.Dispose()
    }
    $bmp.Save($Path,[System.Drawing.Imaging.ImageFormat]::Bmp)
  } finally { $g.Dispose();$bmp.Dispose();$fg.Dispose();$white.Dispose();$muted.Dispose();$pen.Dispose() }
}
New-BrandBitmap (Join-Path $PSScriptRoot 'wizard-brand.bmp') 164 314 $true
New-BrandBitmap (Join-Path $PSScriptRoot 'wizard-small.bmp') 55 55 $false
