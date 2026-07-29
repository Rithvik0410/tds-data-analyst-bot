"""
Runs agent-generated Python in a subprocess so the LLM can do real
computation (pandas groupbys, rates, sorting) instead of guessing numbers.

- Has network access (so pandas.read_csv(url) etc. work directly).
- Has a wall-clock timeout to avoid runaway loops burning the VM.
- Captures stdout; the agent is instructed to print() whatever it needs
  back (results, intermediate values), since that's what's returned.
"""
import os
import subprocess
import sys
import tempfile

TIMEOUT = int(os.environ.get("PYTHON_EXEC_TIMEOUT", "30"))

_PRELUDE = """\
import pandas as pd
import numpy as np
"""


def python_exec(code: str) -> dict:
    full_code = _PRELUDE + "\n" + code
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(full_code)
        script_path = f.name

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        return {
            "stdout": proc.stdout[-6000:],
            "stderr": proc.stderr[-2000:],
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Execution timed out after {TIMEOUT}s"}
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
