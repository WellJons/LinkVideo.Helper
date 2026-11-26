param([Parameter(Mandatory=$true)][string]$Setup,[Parameter(Mandatory=$true)][string]$Uninstaller,[Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WizardCapture {
 [StructLayout(LayoutKind.Sequential)] public struct RECT {public int Left,Top,Right,Bottom;}
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
}
"@
New-Item -ItemType Directory -Force $Output | Out-Null
foreach($mode in @('setup','uninstall')){
 $exe=if($mode -eq 'setup'){$Setup}else{$Uninstaller}
 $pages=if($mode -eq 'setup'){@('welcome','options','progress','finish')}else{@('welcome','progress','finish')}
 foreach($page in $pages){
  $args=@('--layout-preview');if($page -ne 'welcome'){$args+="--$page"}
  $p=Start-Process -FilePath $exe -ArgumentList $args -PassThru
  try{
   for($n=0;$n -lt 100;$n++){Start-Sleep -Milliseconds 100;$p.Refresh();if($p.MainWindowHandle -ne 0){break};if($p.HasExited){throw "Preview exited: $mode/$page"}}
   $h=$p.MainWindowHandle
   if($h -eq 0){throw "Missing wizard: $mode/$page"}
   Start-Sleep -Milliseconds 350
   $r=New-Object WizardCapture+RECT
   if(![WizardCapture]::GetWindowRect($h,[ref]$r)){throw 'GetWindowRect failed'}
   $bmp=[Drawing.Bitmap]::new(($r.Right-$r.Left),($r.Bottom-$r.Top))
   $g=[Drawing.Graphics]::FromImage($bmp)
   try{
    $dc=$g.GetHdc();try{$ok=[WizardCapture]::PrintWindow($h,$dc,2)}finally{$g.ReleaseHdc($dc)}
    if(!$ok){throw "PrintWindow failed: $mode/$page"}
    $bmp.Save((Join-Path $Output "$mode-$page.png"),[Drawing.Imaging.ImageFormat]::Png)
   }finally{$g.Dispose();$bmp.Dispose()}
  }finally{if(!$p.HasExited){$p.CloseMainWindow()|Out-Null;if(!$p.WaitForExit(3000)){$p.Kill()}}}
 }
}
