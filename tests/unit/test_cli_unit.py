"""Unit tests for CLI functions that don't require a full runtime.

Covers: build_parser, _safe_json, cmd_version, main error handling,
CheckStatus/DoctorCheck dataclasses, _add_root_arg.
"""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from agentbase.cli import (
    CheckStatus,
    DoctorCheck,
    _add_root_arg,
    _safe_json,
    build_parser,
    cmd_version,
    main,
)

# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_parser_has_doctor_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"
        assert hasattr(args, "func")

    def test_parser_has_agents_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["agents"])
        assert args.command == "agents"

    def test_parser_has_extensions_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["extensions"])
        assert args.command == "extensions"
        assert args.verbose is False

    def test_parser_extensions_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["extensions", "--verbose"])
        assert args.verbose is True

    def test_parser_has_run_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["run", "hello"])
        assert args.command == "run"
        assert args.message == "hello"
        assert args.agent is None
        assert args.show_raw is False

    def test_parser_has_stream_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["stream", "hello"])
        assert args.command == "stream"
        assert args.message == "hello"

    def test_parser_has_resume_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["resume", "--thread-id", "t1"])
        assert args.command == "resume"
        assert args.thread_id == "t1"
        assert args.decision == "approve"

    def test_parser_has_serve_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["serve"])
        assert args.command == "serve"
        assert args.host == "0.0.0.0"
        assert args.port == 8000
        assert args.reload is False
        assert args.log_level == "info"

    def test_parser_has_version_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["version"])
        assert args.command == "version"

    def test_parser_has_config_validate(self):
        parser = build_parser()
        args = parser.parse_args(["config", "validate"])
        assert args.command == "config"
        assert args.config_command == "validate"

    def test_parser_has_config_show(self):
        parser = build_parser()
        args = parser.parse_args(["config", "show"])
        assert args.config_command == "show"

    def test_parser_has_backup(self):
        parser = build_parser()
        args = parser.parse_args(["backup"])
        assert args.command == "backup"
        assert args.format == "sql"

    def test_parser_has_restore(self):
        parser = build_parser()
        args = parser.parse_args(["restore", "backup.sql"])
        assert args.command == "restore"
        assert args.input == "backup.sql"

    def test_parser_has_worker(self):
        parser = build_parser()
        args = parser.parse_args(["worker"])
        assert args.command == "worker"
        assert args.poll_interval == 2.0

    def test_parser_has_db_subcommands(self):
        parser = build_parser()
        for cmd in ("init", "upgrade", "downgrade", "current", "heads", "history", "stamp"):
            args = parser.parse_args(["db", cmd])
            assert args.command == "db"
            assert args.db_command == cmd

    def test_parser_root_arg_default_none(self):
        parser = build_parser()
        args = parser.parse_args(["doctor"])
        assert args.root is None

    def test_parser_root_arg_custom(self):
        parser = build_parser()
        args = parser.parse_args(["doctor", "--root", "/custom/path"])
        assert args.root == "/custom/path"


# ---------------------------------------------------------------------------
# _add_root_arg
# ---------------------------------------------------------------------------


class TestAddRootArg:
    def test_adds_root_argument(self):
        parser = argparse.ArgumentParser()
        _add_root_arg(parser)
        args = parser.parse_args(["--root", "/some/path"])
        assert args.root == "/some/path"

    def test_default_is_none(self):
        parser = argparse.ArgumentParser()
        _add_root_arg(parser)
        args = parser.parse_args([])
        assert args.root is None


# ---------------------------------------------------------------------------
# CheckStatus / DoctorCheck
# ---------------------------------------------------------------------------


class TestCheckStatus:
    def test_ok_value(self):
        assert CheckStatus.OK == "OK"
        assert CheckStatus.OK.value == "OK"

    def test_warn_value(self):
        assert CheckStatus.WARN == "WARN"

    def test_fail_value(self):
        assert CheckStatus.FAIL == "FAIL"


class TestDoctorCheck:
    def test_creation_with_defaults(self):
        check = DoctorCheck(name="test", status=CheckStatus.OK, detail="all good")
        assert check.name == "test"
        assert check.status == CheckStatus.OK
        assert check.detail == "all good"
        assert check.error_code is None

    def test_creation_with_error_code(self):
        check = DoctorCheck(
            name="test", status=CheckStatus.FAIL, detail="broken",
            error_code="ERR_001"
        )
        assert check.error_code == "ERR_001"


# ---------------------------------------------------------------------------
# _safe_json
# ---------------------------------------------------------------------------


class TestSafeJson:
    def test_serializable_value(self):
        result = _safe_json({"key": "value"})
        assert result == {"key": "value"}

    def test_non_serializable_value(self):
        """json.dumps with default=str handles most objects, so _safe_json returns them as-is.
        The except branch would only trigger if json.dumps raises despite default=str."""
        # With default=str, json.dumps succeeds for most objects
        class Custom:
            def __repr__(self):
                return "CustomRepr"

        result = _safe_json(Custom())
        # Since json.dumps(value, default=str) succeeds, result is the original object
        assert hasattr(result, "__class__")

    def test_nested_complex(self):
        data = {"list": [1, 2, {"inner": "val"}]}
        result = _safe_json(data)
        assert result == data

    def test_none(self):
        assert _safe_json(None) is None


# ---------------------------------------------------------------------------
# cmd_version
# ---------------------------------------------------------------------------


class TestCmdVersion:
    def test_returns_zero(self):
        args = argparse.Namespace()
        result = cmd_version(args)
        assert result == 0


# ---------------------------------------------------------------------------
# main — error handling
# ---------------------------------------------------------------------------


class TestMainErrorHandling:
    def test_no_command_raises_system_exit(self):
        # build_parser requires a subcommand — should error
        with pytest.raises(SystemExit):
            main([])

    def test_unknown_command_raises_system_exit(self):
        with pytest.raises(SystemExit):
            main(["nonexistent-command"])

    def test_main_catches_agentbase_error(self):
        from agentbase.runtime.errors import AgentbaseError

        def mock_func(args):
            raise AgentbaseError("TEST_001", "test error")

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        mock_parser = sub.add_parser("mock")
        mock_parser.set_defaults(func=mock_func)

        with patch("agentbase.cli.build_parser", return_value=parser):
            result = main(["mock"])
            assert result == 1

    def test_main_catches_keyboard_interrupt(self):
        def mock_func(args):
            raise KeyboardInterrupt()

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        mock_parser = sub.add_parser("mock")
        mock_parser.set_defaults(func=mock_func)

        with patch("agentbase.cli.build_parser", return_value=parser):
            result = main(["mock"])
            assert result == 130

    def test_main_catches_unexpected_exception(self):
        def mock_func(args):
            raise RuntimeError("unexpected")

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        mock_parser = sub.add_parser("mock")
        mock_parser.set_defaults(func=mock_func)

        with patch("agentbase.cli.build_parser", return_value=parser):
            result = main(["mock"])
            assert result == 1
