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
    --session|--from|--between|--compare-session|--default-session)
      while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-sessions 2>/dev/null) ;;
    --to)
      if [[ " ${COMP_WORDS[*]} " != *" copy "* && " ${COMP_WORDS[*]} " != *" rename "* ]]; then
        while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-sessions 2>/dev/null)
      fi ;;
    use)
      while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-sessions 2>/dev/null)
      while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-options "${COMP_WORDS[@]:1:COMP_CWORD-1}" 2>/dev/null) ;;
    -s)
      if [[ " ${COMP_WORDS[*]} " != *" path "* ]]; then
        while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-sessions 2>/dev/null)
      else
        while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-options "${COMP_WORDS[@]:1:COMP_CWORD-1}" 2>/dev/null)
      fi ;;
    --branch-protection) choices=(true false) ;;
    --file|-f)
      while IFS= read -r item; do choices+=("$item"); done < <(command staged "$file_helper" 2>/dev/null) ;;
    --shell)
      choices=(bash zsh powershell) ;;
    *)
      if (( COMP_CWORD >= 2 )) && [[ "${COMP_WORDS[COMP_CWORD-2]}" == --between ]]; then
        while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-sessions 2>/dev/null)
      else
        while IFS= read -r item; do choices+=("$item"); done < <(command staged --complete-options "${COMP_WORDS[@]:1:COMP_CWORD-1}" 2>/dev/null)
      fi
      ;;
  esac
  COMPREPLY=()
  local restore_case=0
  shopt -q nocasematch || restore_case=1
  shopt -s nocasematch
  for item in "${choices[@]}"; do
    if [[ "$item" == "$cur"* ]] ||
       { [[ "$prev" == --file || "$prev" == -f ]] && [[ "$item" == *"$cur"* ]]; }; then
      COMPREPLY+=("$item")
    fi
  done
  if (( restore_case )); then shopt -u nocasematch; fi
}
complete -F _staged staged
