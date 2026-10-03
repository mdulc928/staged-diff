_staged() {
  local cur="${COMP_WORDS[COMP_CWORD]}" prev="${COMP_WORDS[COMP_CWORD-1]}" item
  local -a choices=()
  case "$prev" in
    --tool|-t) while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-tools 2>/dev/null) ;;
    --session|-s|--from|--to|--between|--compare-session|use)
      while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-sessions 2>/dev/null) ;;
    *)
      choices=(init diff apply clean path use set set-tool migrate install-skill all --help --session --tool --root --force --yes --file --apply --between --compare-session)
      while IFS= read -r item; do choices+=("$item"); done < <(command staged --list 2>/dev/null)
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
