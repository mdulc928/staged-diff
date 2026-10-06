Register-ArgumentCompleter -Native -CommandName staged -ScriptBlock {
  param($wordToComplete, $commandAst, $cursorPosition)
  $words = @($commandAst.CommandElements | Where-Object { $_.Extent.StartOffset -lt $cursorPosition } | ForEach-Object { $_.Extent.Text })
  $previous = if ($wordToComplete) { $words[-2] } else { $words[-1] }
  $contextWords = @($words | Select-Object -Skip 1)
  if ($wordToComplete) { $contextWords = @($contextWords | Select-Object -SkipLast 1) }
  $fileHelper = if ($words -contains 'open') {
    if ($words -contains '--meta') { '--complete-meta' } else { '--complete-open' }
  } else { '--list' }
  $choices = switch ($previous) {
    { $_ -in '--tool','-t' } { & staged --complete-tools; break }
    { $_ -in '--session','--from','--to','--between','--compare-session','--default-session' } { & staged --complete-sessions; break }
    'use' { & staged --complete-sessions; & staged --complete-options @contextWords; break }
    '-s' { if ($contextWords -contains 'path') { & staged --complete-options @contextWords } else { & staged --complete-sessions }; break }
    '--branch-protection' { 'true','false'; break }
    { $_ -in '--file','-f' } { & staged $fileHelper; break }
    '--shell' { 'bash','zsh','powershell'; break }
    default {
      if ($contextWords.Count -ge 2 -and $contextWords[-2] -eq '--between') {
        & staged --complete-sessions
      } else {
        & staged --complete-options @contextWords
      }
    }
  }
  $choices | Where-Object {
    $_.StartsWith($wordToComplete, [System.StringComparison]::OrdinalIgnoreCase) -or
      (($previous -in '--file','-f') -and
        $_.IndexOf($wordToComplete, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
  } | ForEach-Object {
    $quoted = "'" + $_.Replace("'", "''") + "'"
    [System.Management.Automation.CompletionResult]::new($quoted, $_, 'ParameterValue', $_)
  }
}
