Register-ArgumentCompleter -Native -CommandName staged -ScriptBlock {
  param($wordToComplete, $commandAst, $cursorPosition)
  $words = @($commandAst.CommandElements | ForEach-Object { $_.Extent.Text })
  $previous = if ($wordToComplete) { $words[-2] } else { $words[-1] }
  $choices = switch ($previous) {
    { $_ -in '--tool','-t' } { & staged --complete-tools; break }
    { $_ -in '--session','-s','--from','--to','--between','--compare-session','use' } { & staged --complete-sessions; break }
    default { 'init','diff','apply','clean','path','use','set','set-tool','migrate','install-skill','all'; & staged --list }
  }
  $choices | Where-Object { $_.StartsWith($wordToComplete, [System.StringComparison]::OrdinalIgnoreCase) } | ForEach-Object {
    $quoted = "'" + $_.Replace("'", "''") + "'"
    [System.Management.Automation.CompletionResult]::new($quoted, $_, 'ParameterValue', $_)
  }
}
