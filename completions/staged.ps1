Register-ArgumentCompleter -Native -CommandName staged -ScriptBlock {
  param($wordToComplete, $commandAst, $cursorPosition)
  $words = @($commandAst.CommandElements | ForEach-Object { $_.Extent.Text })
  $previous = if ($wordToComplete) { $words[-2] } else { $words[-1] }
  $fileHelper = if ($words -contains 'open') {
    if ($words -contains '--meta') { '--complete-meta' } else { '--complete-open' }
  } else { '--list' }
  $choices = switch ($previous) {
    { $_ -in '--tool','-t' } { & staged --complete-tools; break }
    { $_ -in '--session','-s','--from','--to','--between','--compare-session','use' } { & staged --complete-sessions; break }
    { $_ -in '--file','-f' } { & staged $fileHelper; break }
    '--shell' { 'bash','zsh','powershell'; break }
    'install-completion' { '--shell'; break }
    default { 'init','diff','apply','clean','path','open','use','set','set-tool','migrate','install-skill','install-completion','--all','--meta','--help','-h','--session','-s','--tool','-t','--root','--force','--yes','-y','--clear','--file','-f','--apply','-a','--clean','-c','--between','--compare-session' }
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
