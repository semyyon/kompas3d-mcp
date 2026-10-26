param([int]$Seconds = 110, [string]$Button = "Экспортировать")
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
if (-not ("WDog" -as [type])) {
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WDog {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
}
"@
}
$kp = (Get-Process KOMPAS -ErrorAction SilentlyContinue | Select-Object -First 1).Id
if (-not $kp) { "KOMPAS not running"; exit }
$deadline = (Get-Date).AddSeconds($Seconds)
$clicked = $false
while ((Get-Date) -lt $deadline -and -not $clicked) {
  Start-Sleep -Milliseconds 1200
  $cands = New-Object System.Collections.Generic.List[IntPtr]
  [WDog]::EnumWindows({ param($h, $l)
    $p = 0; [WDog]::GetWindowThreadProcessId($h, [ref]$p) | Out-Null
    if ($p -eq $kp -and [WDog]::IsWindowVisible($h) -and [WDog]::IsWindowEnabled($h)) { $cands.Add($h) }
    return $true }, [IntPtr]::Zero) | Out-Null
  foreach ($h in $cands) {
    try {
      $el = [System.Windows.Automation.AutomationElement]::FromHandle($h)
      $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $Button)
      $btn = $el.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
      if ($btn -ne $null) {
        $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        "Clicked '$Button' at $(Get-Date -Format HH:mm:ss)"; $clicked = $true; break
      }
    } catch {}
  }
}
if (-not $clicked) { "No dialog within $Seconds s" }
