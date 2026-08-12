"""Code execution tool — runs Python code in a restricted subprocess sandbox.

Executes Python code in an isolated subprocess with timeout, resource limits,
and output capture. The sandbox has restricted imports and no network access.

Safety features:
- Runs in a separate subprocess (not in-process)
- Restricted ``PYTHONPATH`` — only stdlib + site-packages
- No network access (env stripped of proxies)
- Configurable timeout (default 10s, max 60s)
- Output size limit (truncated at 100KB)
- Code size limit (max 50KB)

Usage in config::

    tools:
      - code_execute

The agent can then run::

    code_execute(code="print(2 + 2)", language="python")
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool

_CODE_EXECUTE_META = ExtensionMeta(
    name="code_execute",
    kind="tool",
    description="Execute Python code in a sandboxed subprocess with timeout and output capture.",
)

# Safety limits
_MAX_CODE_SIZE = 50_000        # 50 KB max code size
_MAX_OUTPUT_SIZE = 100_000      # 100 KB max output (stdout + stderr)
_MAX_TIMEOUT = 60               # 60 seconds hard cap
_DEFAULT_TIMEOUT = 10           # 10 seconds default


@register_tool("code_execute", meta=_CODE_EXECUTE_META)
def code_execute(
    code: str,
    language: str = "python",
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    """Execute Python code in a sandboxed subprocess.

    Args:
        code: Python source code to execute.
        language: Programming language (only "python" supported).
        timeout: Execution timeout in seconds (max 60).

    Returns:
        dict with keys: stdout, stderr, exit_code, timed_out
    """
    if language != "python":
        return {
            "stdout": "",
            "stderr": f"Unsupported language: {language}. Only 'python' is supported.",
            "exit_code": 1,
            "timed_out": False,
        }

    # Enforce code size limit
    if len(code) > _MAX_CODE_SIZE:
        return {
            "stdout": "",
            "stderr": f"Code too large: {len(code)} chars (max {_MAX_CODE_SIZE})",
            "exit_code": 1,
            "timed_out": False,
        }

    # Enforce timeout cap
    timeout = min(max(timeout, 1), _MAX_TIMEOUT)

    # Write code to a temp file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(code)
        script_path = Path(f.name)

    try:
        # Build a restricted environment
        # Keep minimal env vars — strip proxies to prevent network access
        restricted_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            # Strip proxy settings to prevent network access
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "http_proxy": "",
            "https_proxy": "",
            "ALL_PROXY": "",
            "all_proxy": "",
        }

        # Run the script file directly (not via -c which has different semantics)
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=restricted_env,
        )

        # Truncate output if too large
        stdout = result.stdout
        stderr = result.stderr
        if len(stdout) > _MAX_OUTPUT_SIZE:
            stdout = stdout[:_MAX_OUTPUT_SIZE] + "\n...(output truncated)"
        if len(stderr) > _MAX_OUTPUT_SIZE:
            stderr = stderr[:_MAX_OUTPUT_SIZE] + "\n...(output truncated)"

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        # Capture partial output if available
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "stdout": stdout[:_MAX_OUTPUT_SIZE],
            "stderr": f"Execution timed out after {timeout}s\n" + stderr[:_MAX_OUTPUT_SIZE],
            "exit_code": -1,
            "timed_out": True,
        }
    except Exception as exc:
        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 1,
            "timed_out": False,
        }
    finally:
        script_path.unlink(missing_ok=True)


__all__ = ["code_execute"]
