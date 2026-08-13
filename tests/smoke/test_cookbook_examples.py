"""Smoke tests for the Cookbook example scripts (F1).

Each test runs the corresponding example script as a subprocess and verifies
that it exits successfully (exit code 0) and produces expected output.

Additionally tests that --help works for each script.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"

# All example scripts to test
EXAMPLE_SCRIPTS = [
    "custom_embedding.py",
    "custom_search.py",
    "custom_queue.py",
    "custom_tracer.py",
    "custom_parser.py",
    "custom_mcp.py",
    "custom_graph.py",
    "custom_tool.py",
    "custom_middleware.py",
    "switch_storage.py",
    "switch_checkpointer.py",
]


def _run_script(script_name: str, *args: str) -> tuple[int, str, str]:
    """Run a script and return (exit_code, stdout, stderr)."""
    script_path = EXAMPLES_DIR / script_name
    cmd = [sys.executable, str(script_path), *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(script_path.parent.parent),  # run from project root
    )
    return result.returncode, result.stdout, result.stderr


@pytest.mark.parametrize("script_name", EXAMPLE_SCRIPTS)
class TestExampleScripts:
    """Test that each example script runs successfully."""

    def test_script_runs(self, script_name: str):
        """Script should exit with code 0 and produce output."""
        exit_code, stdout, stderr = _run_script(script_name)
        assert exit_code == 0, (
            f"{script_name} failed with exit code {exit_code}\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )
        # Should produce some output
        assert len(stdout) > 0 or len(stderr) > 0, (
            f"{script_name} produced no output"
        )

    def test_script_help(self, script_name: str):
        """Script should support --help and exit with code 0."""
        exit_code, stdout, stderr = _run_script(script_name, "--help")
        assert exit_code == 0, (
            f"{script_name} --help failed with exit code {exit_code}\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )
        # --help output should contain "usage" or "description"
        combined = stdout + stderr
        assert "usage" in combined.lower() or "description" in combined.lower(), (
            f"{script_name} --help did not produce help text"
        )

    def test_script_has_cookbook_header(self, script_name: str):
        """Script output should contain 'Cookbook' to identify it."""
        exit_code, stdout, stderr = _run_script(script_name)
        assert exit_code == 0
        combined = stdout + stderr
        assert "Cookbook" in combined or "示例完成" in combined, (
            f"{script_name} did not produce Cookbook-branded output"
        )


class TestExampleScriptContent:
    """Test that example scripts have proper structure."""

    def test_all_scripts_exist(self):
        """All expected example scripts should exist."""
        for script_name in EXAMPLE_SCRIPTS:
            script_path = EXAMPLES_DIR / script_name
            assert script_path.exists(), f"Example script not found: {script_name}"

    def test_all_scripts_have_main_guard(self):
        """All scripts should have if __name__ == '__main__' guard."""
        for script_name in EXAMPLE_SCRIPTS:
            script_path = EXAMPLES_DIR / script_name
            content = script_path.read_text(encoding="utf-8")
            assert "__main__" in content, (
                f"{script_name} missing __main__ guard"
            )

    def test_all_scripts_have_argparse(self):
        """All scripts should use argparse for --help support."""
        for script_name in EXAMPLE_SCRIPTS:
            script_path = EXAMPLES_DIR / script_name
            content = script_path.read_text(encoding="utf-8")
            assert "argparse" in content, (
                f"{script_name} missing argparse (needed for --help)"
            )

    def test_all_scripts_have_docstring(self):
        """All scripts should have a module-level docstring."""
        for script_name in EXAMPLE_SCRIPTS:
            script_path = EXAMPLES_DIR / script_name
            content = script_path.read_text(encoding="utf-8")
            # Check for triple-quoted docstring near the top
            assert '"""' in content[:500], (
                f"{script_name} missing module-level docstring"
            )


class TestExamplesReadme:
    """Test that the README.md exists and is well-formed."""

    def test_readme_exists(self):
        readme = EXAMPLES_DIR / "README.md"
        assert readme.exists(), "examples/README.md not found"

    def test_readme_lists_all_scripts(self):
        readme = EXAMPLES_DIR / "README.md"
        content = readme.read_text(encoding="utf-8")
        for script_name in EXAMPLE_SCRIPTS:
            assert script_name in content, (
                f"{script_name} not mentioned in examples/README.md"
            )
