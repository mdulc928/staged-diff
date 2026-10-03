_staged() {
  local cur="${COMP_WORDS[COMP_CWORD]}" prev="${COMP_WORDS[COMP_CWORD-1]}" item
  local -a choices=()
  local file_helper=--list
  if [[ " ${COMP_WORDS[*]} " == *" open "* ]]; then
    file_helper=--complete-open
    if [[ " ${COMP_WORDS[*]} " == *" --meta "* ]]; then file_helper=--complete-meta; fi
  fi
  case "$prev" in
    --tool|-t) while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-tools 2>/dev/null) ;;
    --session|-s|--from|--to|--between|--compare-session|use)
      while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-sessions 2>/dev/null) ;;
    --file|-f)
      while IFS= read -r item; do choices+=("$item"); done < <(command staged "$file_helper" 2>/dev/null) ;;
    --shell)
      choices=(bash zsh powershell) ;;
    install-completion)
      choices=(--shell) ;;
    *)
      choices=(init diff apply clean path open use set set-tool migrate install-skill install-completion --all --meta --help -h --session -s --tool -t --root --force --yes -y --clear --file -f --apply -a --clean -c --between --compare-session)
      ;;
  esac
  COMPREPLY=()
  local restore_case=0
  shopt -q nocasematch || restore_case=1
  shopt -s nocasematch
  for item in "${choices[@]}"; do
    [[ "$item" == "$cur"* ]] && COMPREPLY+=("$item")
  done
  if (( restore_case )); then shopt -u nocasematch; fi
}
complete -F _staged staged
