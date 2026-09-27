# shell_timemachine bash integration.  Load with:
#   eval "$(python3 /path/to/shell_timemachine.py hook bash)"
__tm_py=@TM_PY@
__tm_script=@TM_SCRIPT@
export TM_HOME="${TM_HOME:-$HOME/.shell_timemachine}"
mkdir -p "$TM_HOME" && chmod 700 "$TM_HOME" 2>/dev/null
__tm_at_prompt=   # set by the first prompt; avoids recording rc-file lines
__tm_armed=
__tm_start=

# DEBUG trap = "preexec": fires before every simple command; we only arm on the
# first one after a prompt, and ignore our own prompt machinery.
__tm_preexec() {
  [ -n "$COMP_LINE" ] && return
  [ -z "$__tm_at_prompt" ] && return
  case "$BASH_COMMAND" in __tm_*) return ;; esac
  case ";$PROMPT_COMMAND;" in *";$BASH_COMMAND;"*) return ;; esac
  __tm_at_prompt=
  __tm_armed=1
  __tm_start=$(date +%s.%N)
}

# PROMPT_COMMAND = "precmd": record the command that just finished.
__tm_precmd() {
  local ec=${__tm_ec:-0}
  if [ -n "$__tm_armed" ]; then
    local cmd envf filesf dur
    cmd=$(HISTTIMEFORMAT= builtin history 1 | sed -E 's/^ *[0-9]+\*? *//')
    dur=$(awk -v s="$__tm_start" -v e="$(date +%s.%N)" 'BEGIN{printf "%.3f", e-s}')
    envf="$TM_HOME/.env.$$.$RANDOM"
    filesf="$envf.files"
    # Snapshots are taken synchronously (cheap) so the background recorder
    # cannot observe later commands' effects.
    (umask 077; env -0 > "$envf") 2>/dev/null  # raw env: private, deleted by recorder
    stat -c '%n	%s	%Y	%F' -- * .[!.]* ..?* 2>/dev/null | head -n 400 > "$filesf"
    ( "$__tm_py" "$__tm_script" record --cmd "$cmd" --cwd "$PWD" --exit "$ec" \
        --ts "$__tm_start" --duration "$dur" --env-file "$envf" --files-file "$filesf" \
        --session "$$" >/dev/null 2>&1 & )
  fi
  __tm_armed=
  __tm_at_prompt=1
}

tm()  { "$__tm_py" "$__tm_script" "$@"; }
# tmr CMD... : run and record full output (enables output diffs)
tmr() { "$__tm_py" "$__tm_script" run -- "$*"; }

trap '__tm_preexec' DEBUG
PROMPT_COMMAND="__tm_ec=\$?;${PROMPT_COMMAND:+$PROMPT_COMMAND;}__tm_precmd"
