<#
.SYNOPSIS
  Presses "Add" on Firefox's install prompt for one specific add-on, then "OK"
  on the "was added" notice that follows.

.DESCRIPTION
  Uses Windows UI Automation. To make sure nothing else gets clicked, a button
  is only pressed when it is
    - in a firefox.exe window (Thunderbird uses the same window class),
    - inside one of the browser's own notification popups, not web content,
    - in a popup whose title names this add-on ("<AddonName> hinzufuegen",
      "<AddonName> wurde hinzugefuegt.", "Add <AddonName>?" ...),
    - labelled Add / Hinzufuegen (install prompt) or OK / Okay (notice).
  An open "was added" notice from an earlier install holds back the next
  prompt, so one for this add-on is closed first.
  Exit code 0 when Add was pressed, 1 when no matching prompt appeared in time.

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
$Any = [System.Windows.Automation.Condition]::TrueCondition

# Kept ASCII: Windows PowerShell 5.1 misreads non-ASCII in BOM-less scripts.
$addLabels = @('Add', ('Hinzuf' + [char]0x00FC + 'gen'))
$okLabels = @('OK', 'Okay')

function Find-PopupButton([string]$popupId, [string[]]$labels) {
    $firefoxPids = @(Get-Process firefox -ErrorAction SilentlyContinue | ForEach-Object Id)
    foreach ($window in $AE::RootElement.FindAll($Scope::Children, $Any)) {
        if ($firefoxPids -notcontains $window.Current.ProcessId) { continue }
        $popup = $window.FindFirst($Scope::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition($AE::AutomationIdProperty, $popupId)))
        if (-not $popup) { continue }
        # A popup shows one notification at a time; its title, texts and
        # buttons are siblings. Require the title to name this add-on.
        $named = $false
        foreach ($part in $popup.FindAll($Scope::Children, $Any)) {
            if ($part.Current.Name -like "*$AddonName*") { $named = $true }
        }
        if (-not $named) { continue }
        $buttons = $popup.FindAll($Scope::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::Button)))
        foreach ($button in $buttons) {
            if ($labels -contains $button.Current.Name -and $button.Current.IsEnabled) { return $button }
        }
    }
    return $null
}

function Press($button, [string]$what) {
    $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    Write-Output "Pressed '$($button.Current.Name)' on the $AddonName $what."
}

function Wait-PopupButton([string]$popupId, [string[]]$labels, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    do {
        $button = Find-PopupButton $popupId $labels
        if ($button) { return $button }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $null
}

$leftover = Find-PopupButton 'appMenu-notification-popup' $okLabels
if ($leftover) { Press $leftover 'notice left from an earlier install' }

$add = Wait-PopupButton 'notification-popup' $addLabels $TimeoutSeconds
if (-not $add) {
    Write-Output "No $AddonName install prompt found within $TimeoutSeconds s."
    exit 1
}
# Firefox ignores presses on a fresh install prompt for about a second
# (security.dialog_enable_delay, against click-jacking), although the button
# already reports itself enabled. Wait that out, then press.
Start-Sleep -Milliseconds 1500
$add = Find-PopupButton 'notification-popup' $addLabels
if (-not $add) {
    Write-Output "The $AddonName install prompt went away."
    exit 1
}
Press $add 'install prompt'

$notice = Wait-PopupButton 'appMenu-notification-popup' $okLabels 10
if ($notice) { Press $notice '"was added" notice' }
exit 0
