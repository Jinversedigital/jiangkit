import os
import subprocess
import sys


def jiang(*args, env=None, cwd=None, timeout=120):
    e = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, "-m", "jiangkit", *map(str, args)], capture_output=True,
                          text=True, env=e, cwd=cwd, timeout=timeout)
