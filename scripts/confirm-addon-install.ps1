<#
.SYNOPSIS
  Presses "Add" on Firefox's install prompt for one specific add-on.

.DESCRIPTION
  Uses Windows UI Automation. To make sure nothing else gets clicked, the
  button must be:
    - in a firefox.exe window (Thunderbird uses the same window class),
    - inside the browser's own notification popup, not in web content,
    - in the prompt that names this add-on ("<AddonName> hinzufuegen" / "Add <AddonName>?"),
    - labelled Add / Hinzufuegen.
  Exit code 0 when clicked, 1 when no matching prompt appeared in time.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\confirm-addon-install.ps1 -AddonName ClaudeCodeBrowser
#>
param(
    [Parameter(Mandatory = $true)][string]$AddonName,
    [int]$TimeoutSeconds = 30
)

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]
$Scope = [System.Windows.Automation.TreeScope]

# Kept ASCII: Windows PowerShell 5.1 misreads non-ASCII in BOM-less scripts.
$addLabels = @('Add', ('Hinzuf' + [char]0x00FC + 'gen'))

function Find-InstallButton {
    $firefoxPids = @(Get-Process firefox -ErrorAction SilentlyContinue | ForEach-Object Id)
    if (-not $firefoxPids) { return $null }
    $windows = $AE::RootElement.FindAll($Scope::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($window in $windows) {
        if ($firefoxPids -notcontains $window.Current.ProcessId) { continue }
        $popup = $window.FindFirst($Scope::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition($AE::AutomationIdProperty, 'notification-popup')))
        if (-not $popup) { continue }
        # The popup shows one prompt at a time; its title, permission lists and
        # buttons are siblings. Require the title to name this add-on.
        $parts = $popup.FindAll($Scope::Children, [System.Windows.Automation.Condition]::TrueCondition)
        $isOurPrompt = $false
        foreach ($part in $parts) { if ($part.Current.Name -like "*$AddonName*") { $isOurPrompt = $true } }
        if (-not $isOurPrompt) { continue }
        $buttons = $popup.FindAll($Scope::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::Button)))
        foreach ($button in $buttons) {
            if ($addLabels -contains $button.Current.Name -and $button.Current.IsEnabled) { return $button }
        }
    }
    return $null
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    $button = Find-InstallButton
    if ($button) {
        $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        Write-Output "Clicked '$($button.Current.Name)' on the $AddonName install prompt."
        exit 0
    }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)

Write-Output "No $AddonName install prompt found within $TimeoutSeconds s."
exit 1
