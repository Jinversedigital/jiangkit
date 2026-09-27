# shell_timemachine zsh integration.  Load with:
#   eval "$(python3 /path/to/shell_timemachine.py hook zsh)"
__tm_py=@TM_PY@
__tm_script=@TM_SCRIPT@
export TM_HOME="${TM_HOME:-$HOME/.shell_timemachine}"
mkdir -p "$TM_HOME" && chmod 700 "$TM_HOME" 2>/dev/null
typeset -g __tm_cmd="" __tm_start=""

__tm_preexec() {            # $1 = the command line as typed
  __tm_cmd="$1"
  __tm_start=$(__tm_now)
}

__tm_precmd() {
  local ec=$?
  [[ -z "$__tm_cmd" ]] && return $ec
  local dur envf filesf
  dur=$(( $(__tm_now) - ${__tm_start:-0} ))
  envf="$TM_HOME/.env.$$.$RANDOM"
  filesf="$envf.files"
  (umask 077; env -0 > "$envf") 2>/dev/null  # raw env: private, deleted by recorder
  stat -c '%n	%s	%Y	%F' -- *(ND[1,400]) 2>/dev/null > "$filesf"
  ( "$__tm_py" "$__tm_script" record --cmd "$__tm_cmd" --cwd "$PWD" --exit "$ec" \
      --ts "$__tm_start" --duration "$dur" --env-file "$envf" --files-file "$filesf" \
      --session "$$" >/dev/null 2>&1 & )
  __tm_cmd=""
  return $ec
}

zmodload zsh/datetime 2>/dev/null
# Fall back to date(1) when the zsh/datetime module is unavailable.
__tm_now() { if [[ -n "$EPOCHREALTIME" ]]; then print -r -- $EPOCHREALTIME; else date +%s.%N; fi }
tm()  { "$__tm_py" "$__tm_script" "$@" }
tmr() { "$__tm_py" "$__tm_script" run -- "$*" }
typeset -ga preexec_functions precmd_functions
preexec_functions+=(__tm_preexec)
precmd_functions=(__tm_precmd $precmd_functions)
