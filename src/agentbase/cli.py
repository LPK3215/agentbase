from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

# Force UTF-8 for stdout/stderr to avoid GBK encoding errors on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentbase.bootstrap import build_runtime, resolve_root_dir
from agentbase.registry.backends import backend_registry
from agentbase.registry.checkpointers import checkpointer_registry
from agentbase.registry.middleware import middleware_registry
from agentbase.registry.subagents import subagent_registry
from agentbase.registry.tools import tool_registry
from agentbase.runtime.errors import AgentbaseError
from agentbase.runtime.events import EventType

console = Console(force_terminal=True, legacy_windows=False)


class CheckStatus(str, Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class DoctorCheck:
    name: str
    status: CheckStatus
    detail: str
    error_code: str | None = None


def _add_root_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        default=None,
        help="Project root directory (defaults to current working directory)",
    )


def run_doctor_checks(runtime) -> list[DoctorCheck]:
    root = runtime.root_dir
    app = runtime.app_config
    checks: list[DoctorCheck] = []

    checks.append(DoctorCheck("root", CheckStatus.OK, str(root)))

    config_path = root / "configs" / "default.yaml"
    checks.append(
        DoctorCheck(
            "config",
            CheckStatus.OK if config_path.exists() else CheckStatus.FAIL,
            str(config_path),
            error_code=None if config_path.exists() else "AGENTBASE_CONFIG_001",
        )
    )

    agents_list = runtime.list_agents()
    checks.append(
        DoctorCheck(
            "agents",
            CheckStatus.OK if agents_list else CheckStatus.FAIL,
            ", ".join(agents_list) or "<none>",
            error_code=None if agents_list else "AGENTBASE_CONFIG_001",
        )
    )

    tool_names = tool_registry.names()
    checks.append(DoctorCheck("tools", CheckStatus.OK if tool_names else CheckStatus.WARN, ", ".join(tool_names) or "<none>"))

    mw_names = middleware_registry.names()
    checks.append(DoctorCheck("middleware", CheckStatus.OK if mw_names else CheckStatus.WARN, ", ".join(mw_names) or "<none>"))

    sa_names = subagent_registry.names()
    checks.append(DoctorCheck("subagents", CheckStatus.OK if sa_names else CheckStatus.WARN, ", ".join(sa_names) or "<none>"))

    be_names = backend_registry.names()
    checks.append(DoctorCheck("backends", CheckStatus.OK if be_names else CheckStatus.WARN, ", ".join(be_names) or "<none>"))

    cp_names = checkpointer_registry.names()
    checks.append(DoctorCheck("checkpointers", CheckStatus.OK if cp_names else CheckStatus.WARN, ", ".join(cp_names) or "<none>"))

    checks.append(DoctorCheck("model", CheckStatus.OK, app.model.model_string))

    from agentbase.config.settings import env_get

    key_env = app.model.api_key_env or "OPENAI_API_KEY"
    secret = env_get(key_env, "SILICONFLOW_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "AGNES_API_KEY")
    checks.append(
        DoctorCheck(
            "model_api_key",
            CheckStatus.OK if secret else CheckStatus.FAIL,
            f"resolved via env ({'set' if secret else 'missing'})",
            error_code=None if secret else "AGENTBASE_CONFIG_002",
        )
    )

    workspace = root / app.runtime.workspace_dir
    checks.append(
        DoctorCheck(
            "workspace",
            CheckStatus.OK if workspace.exists() else CheckStatus.WARN,
            str(workspace),
        )
    )

    skills_dir = root / app.runtime.workspace_dir / "skills"
    if not skills_dir.exists() or not any(skills_dir.iterdir()):
        checks.append(DoctorCheck("skills", CheckStatus.WARN, "skills dir empty or missing"))
    else:
        checks.append(DoctorCheck("skills", CheckStatus.OK, ", ".join(sorted(p.name for p in skills_dir.iterdir()))))

    memory_file = root / app.runtime.workspace_dir / "memory" / "AGENTS.md"
    if memory_file.exists():
        content = memory_file.read_text(encoding="utf-8")
        if "## Editing Conventions" in content:
            checks.append(DoctorCheck("memory", CheckStatus.OK, str(memory_file)))
        else:
            checks.append(DoctorCheck("memory", CheckStatus.WARN, "missing '## Editing Conventions' section"))
    else:
        checks.append(DoctorCheck("memory", CheckStatus.WARN, "AGENTS.md not found"))

    for agent_name in agents_list:
        try:
            agent_cfg = runtime.get_agent_config(agent_name)
            runtime.factory.build(agent_cfg)
            checks.append(DoctorCheck(f"assembly:{agent_name}", CheckStatus.OK, f"assembled {agent_name}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(
                DoctorCheck(
                    f"assembly:{agent_name}",
                    CheckStatus.FAIL,
                    str(exc),
                    error_code="AGENTBASE_FACTORY_003",
                )
            )

    return checks


def cmd_doctor(args: argparse.Namespace) -> int:
    root = resolve_root_dir(args.root)
    runtime = build_runtime(root)
    checks = run_doctor_checks(runtime)

    table = Table(title="agentbase doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    table.add_column("Error Code")

    for check in checks:
        style = {"OK": "green", "WARN": "yellow", "FAIL": "red"}[check.status.value]
        table.add_row(check.name, f"[{style}]{check.status.value}[/{style}]", check.detail, check.error_code or "")

    console.print(table)
    return 1 if any(c.status == CheckStatus.FAIL for c in checks) else 0


def cmd_list_agents(args: argparse.Namespace) -> int:
    runtime = build_runtime(resolve_root_dir(args.root))
    for name in runtime.list_agents():
        cfg = runtime.get_agent_config(name)
        console.print(f"- {name}: {cfg.description or 'no description'}")
    return 0


def cmd_list_extensions(args: argparse.Namespace) -> int:
    build_runtime(resolve_root_dir(args.root))
    verbose = getattr(args, "verbose", False)
    mapping = {
        "tools": tool_registry,
        "middleware": middleware_registry,
        "subagents": subagent_registry,
    }
    if verbose:
        for kind, registry in mapping.items():
            table = Table(title=f"[{kind}]")
            table.add_column("name")
            table.add_column("kind")
            table.add_column("description")
            table.add_column("requires_context")
            table.add_column("default_enabled")
            for name in registry.names():
                meta = registry.get_meta(name)
                if meta is not None:
                    table.add_row(
                        meta.name,
                        meta.kind,
                        meta.description,
                        ", ".join(meta.requires_context) or "<none>",
                        str(meta.default_enabled),
                    )
                else:
                    table.add_row(name, kind, "<no meta>", "<none>", "<unknown>")
            console.print(table)
        for kind in ("backends", "checkpointers"):
            registry = backend_registry if kind == "backends" else checkpointer_registry
            console.print(f"[{kind}]")
            for name in registry.names():
                console.print(f"  - {name}")
    else:
        full_mapping = {
            "tools": tool_registry.names(),
            "middleware": middleware_registry.names(),
            "subagents": subagent_registry.names(),
            "backends": backend_registry.names(),
            "checkpointers": checkpointer_registry.names(),
        }
        for kind, names in full_mapping.items():
            console.print(f"[{kind}]")
            for name in names:
                console.print(f"  - {name}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    runtime = build_runtime(resolve_root_dir(args.root))
    agent_name = args.agent or runtime.app_config.runtime.default_agent
    agent = runtime.get_agent(agent_name)
    result = runtime.runner.invoke(
        agent=agent,
        agent_name=agent_name,
        message=args.message,
        thread_id=args.thread_id,
    )
    console.print(Panel(result["output_text"] or "<empty>", title=f"agent:{agent_name}"))
    console.print(f"thread_id={result['thread_id']}")
    if args.show_raw:
        console.print_json(json.dumps(_safe_json(result["result"]), ensure_ascii=True, default=str))
    return 0


def cmd_stream(args: argparse.Namespace) -> int:
    runtime = build_runtime(resolve_root_dir(args.root))
    agent_name = args.agent or runtime.app_config.runtime.default_agent
    agent = runtime.get_agent(agent_name)

    final_text = ""
    thread_id = None
    for event in runtime.runner.stream(
        agent=agent,
        agent_name=agent_name,
        message=args.message,
        thread_id=args.thread_id,
    ):
        thread_id = event.thread_id
        if event.type == EventType.MESSAGE_DELTA:
            text = str(event.data.get("text") or "")
            if text:
                console.print(text, end="")
                final_text += text
        elif event.type == EventType.MESSAGE_FINAL:
            text = str(event.data.get("text") or "")
            if text and text not in final_text:
                console.print(text)
                final_text = text
        elif event.type == EventType.TOOL_START:
            console.print(f"\n[tool.start] {event.data}")
        elif event.type == EventType.TOOL_END:
            console.print(f"\n[tool.end] {event.data}")
        elif event.type == EventType.RUN_ERROR:
            console.print(f"\n[error] {event.data}")
            return 1
        elif event.type == EventType.RUN_FINISHED:
            if not final_text:
                final_text = str(event.data.get("output_text") or "")
            console.print()
            console.print(f"thread_id={thread_id}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    runtime = build_runtime(resolve_root_dir(args.root))
    agent_name = args.agent or runtime.app_config.runtime.default_agent
    agent = runtime.get_agent(agent_name)

    decision: Any
    if args.decision_json:
        decision = json.loads(args.decision_json)
    else:
        decision = {"type": args.decision}

    result = runtime.runner.resume(
        agent=agent,
        agent_name=agent_name,
        thread_id=args.thread_id,
        decision=decision,
    )
    console.print(Panel(result["output_text"] or "<empty>", title=f"resume:{agent_name}"))
    console.print(f"thread_id={result['thread_id']}")
    return 0


def _safe_json(value: Any) -> Any:
    try:
        json.dumps(value, default=str)
        return value
    except Exception:
        return repr(value)


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the FastAPI server."""
    import uvicorn

    root = resolve_root_dir(args.root)
    # Build runtime before starting server so config errors surface early
    build_runtime(root)

    console.print(f"[green]Starting agentbase server on {args.host}:{args.port}[/green]")
    console.print(f"  API docs: http://{args.host}:{args.port}/docs")
    console.print(f"  Health:   http://{args.host}:{args.port}/health")

    uvicorn.run(
        "agentbase.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Print the agentbase version."""
    from agentbase.config.schema import AppInfo

    info = AppInfo()
    console.print(f"agentbase v{info.version}")
    console.print(f"  Python: {__import__('sys').version.split()[0]}")
    console.print(f"  Platform: {__import__('platform').platform()}")
    return 0


def cmd_config_validate(args: argparse.Namespace) -> int:
    """Validate configuration files without starting the server."""
    from pathlib import Path

    from agentbase.config.loader import list_agent_names, load_agent_config, load_app_config
    from agentbase.runtime.errors import AgentbaseError

    root = resolve_root_dir(args.root)
    console.print(f"[cyan]Validating configuration at {root}[/cyan]")

    errors: list[str] = []
    warnings: list[str] = []

    # 1. Validate app config
    try:
        app_config = load_app_config(root)
        console.print(f"  [green]OK[/green] app config: {app_config.app.name} (env={app_config.app.env})")
        console.print(f"    model: {app_config.model.provider}:{app_config.model.name}")
        console.print(f"    storage: {app_config.storage.type}")
        console.print(f"    checkpointer: {app_config.checkpointer.type}")
        console.print(f"    auth: {app_config.auth.type}")
        console.print(f"    rate_limit: enabled={app_config.rate_limit.enabled}")
    except AgentbaseError as exc:
        errors.append(f"app config: {exc}")
        console.print(f"  [red]FAIL[/red] app config: {exc}")
    except Exception as exc:
        errors.append(f"app config (unexpected): {exc}")
        console.print(f"  [red]FAIL[/red] app config: {exc}")

    # 2. Validate agent configs
    config_dir = root / "configs"
    try:
        agent_names = list_agent_names(config_dir)
        if not agent_names:
            warnings.append("No agent configs found")
            console.print(f"  [yellow]WARN[/yellow] no agent configs found")
        for name in agent_names:
            try:
                cfg = load_agent_config(config_dir, name)
                console.print(f"  [green]OK[/green] agent '{name}': tools={len(cfg.tools)} middleware={len(cfg.middleware)}")
            except AgentbaseError as exc:
                errors.append(f"agent '{name}': {exc}")
                console.print(f"  [red]FAIL[/red] agent '{name}': {exc}")
    except Exception as exc:
        errors.append(f"agent configs: {exc}")
        console.print(f"  [red]FAIL[/red] agent configs: {exc}")

    # 3. Check workspace structure
    workspace = root / app_config.runtime.workspace_dir if 'app_config' in dir() else root / "workspace"
    for subdir in ["workspace", "uploads", "outputs", "skills", "memory"]:
        path = workspace / subdir if subdir != "skills" else workspace / "skills"
        if not path.exists():
            warnings.append(f"workspace/{subdir} not found")
            console.print(f"  [yellow]WARN[/yellow] workspace/{subdir} not found")

    # Summary
    console.print()
    if errors:
        console.print(f"[red]VALIDATION FAILED: {len(errors)} error(s), {len(warnings)} warning(s)[/red]")
        return 1
    elif warnings:
        console.print(f"[yellow]VALIDATION PASSED with {len(warnings)} warning(s)[/yellow]")
    else:
        console.print(f"[green]VALIDATION PASSED: all checks OK[/green]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentbase", description="Deep Agents backend harness")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Validate configuration and assembly")
    _add_root_arg(doctor)
    doctor.set_defaults(func=cmd_doctor)

    agents = sub.add_parser("agents", help="List agent profiles")
    _add_root_arg(agents)
    agents.set_defaults(func=cmd_list_agents)

    exts = sub.add_parser("extensions", help="List registered extensions")
    _add_root_arg(exts)
    exts.add_argument("--verbose", action="store_true", help="Show extension metadata")
    exts.set_defaults(func=cmd_list_extensions)

    run = sub.add_parser("run", help="Invoke an agent")
    _add_root_arg(run)
    run.add_argument("--agent", default=None)
    run.add_argument("--thread-id", default=None)
    run.add_argument("--show-raw", action="store_true")
    run.add_argument("message")
    run.set_defaults(func=cmd_run)

    stream = sub.add_parser("stream", help="Stream an agent run")
    _add_root_arg(stream)
    stream.add_argument("--agent", default=None)
    stream.add_argument("--thread-id", default=None)
    stream.add_argument("message")
    stream.set_defaults(func=cmd_stream)

    resume = sub.add_parser("resume", help="Resume an interrupted agent run")
    _add_root_arg(resume)
    resume.add_argument("--agent", default=None)
    resume.add_argument("--thread-id", required=True)
    resume.add_argument("--decision", default="approve", choices=["approve", "edit", "reject", "respond"])
    resume.add_argument("--decision-json", default=None)
    resume.set_defaults(func=cmd_resume)

    serve = sub.add_parser("serve", help="Start the FastAPI server")
    _add_root_arg(serve)
    serve.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    serve.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    serve.add_argument("--reload", action="store_true", help="Enable auto-reload")
    serve.add_argument("--log-level", default="info", help="Log level (default: info)")
    serve.set_defaults(func=cmd_serve)

    version = sub.add_parser("version", help="Print version information")
    version.set_defaults(func=cmd_version)

    validate = sub.add_parser("config", help="Configuration management")
    config_sub = validate.add_subparsers(dest="config_command", required=True)
    val_cmd = config_sub.add_parser("validate", help="Validate configuration files")
    _add_root_arg(val_cmd)
    val_cmd.set_defaults(func=cmd_config_validate)

    show_cmd = config_sub.add_parser("show", help="Display resolved configuration")
    _add_root_arg(show_cmd)
    show_cmd.set_defaults(func=cmd_config_show)

    init = sub.add_parser("init", help="Initialize a new agentbase project")
    init.add_argument("path", nargs="?", default=".", help="Project directory (default: current)")
    init.add_argument("--name", default="my-project", help="Project name")
    init.add_argument("--force", action="store_true", help="Overwrite existing files")
    init.add_argument("--dry-run", action="store_true", help="Preview changes without writing files")
    init.set_defaults(func=cmd_init)

    backup = sub.add_parser("backup", help="Backup database to a file")
    _add_root_arg(backup)
    backup.add_argument("--output", "-o", default=None, help="Output file path")
    backup.add_argument("--format", default="sql", choices=["sql", "json"], help="Backup format")
    backup.set_defaults(func=cmd_backup)

    restore = sub.add_parser("restore", help="Restore database from a backup file")
    _add_root_arg(restore)
    restore.add_argument("input", help="Backup file path")
    restore.add_argument("--format", default="sql", choices=["sql", "json"], help="Backup format")
    restore.set_defaults(func=cmd_restore)

    worker = sub.add_parser("worker", help="Start a queue worker process")
    _add_root_arg(worker)
    worker.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between polls when idle")
    worker.set_defaults(func=cmd_worker)

    agent_cmd = sub.add_parser("agent", help="Agent management")
    agent_sub = agent_cmd.add_subparsers(dest="agent_command", required=True)
    create_cmd = agent_sub.add_parser("create", help="Create a new agent config")
    _add_root_arg(create_cmd)
    create_cmd.add_argument("name", help="Agent name")
    create_cmd.add_argument("--description", default="", help="Agent description")
    create_cmd.add_argument("--system-prompt", default="", help="System prompt")
    create_cmd.add_argument("--tools", default="", help="Comma-separated tool names")
    create_cmd.add_argument("--middleware", default="", help="Comma-separated middleware names")
    create_cmd.add_argument("--capabilities", default="", help="Comma-separated capabilities")
    create_cmd.add_argument("--force", action="store_true", help="Overwrite existing agent config")
    create_cmd.set_defaults(func=cmd_agent_create)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize a new agentbase project directory with comprehensive defaults.

    Features:
    - File conflict detection with detailed reporting
    - ``--dry-run`` to preview changes without writing
    - Template variable substitution via the template engine
    - Configurable merge strategy (skip/overwrite)
    """
    from pathlib import Path

    from agentbase.core.template import render

    project_dir = Path(args.path)
    project_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Initializing agentbase project: {args.name}[/cyan]")
    if args.dry_run:
        console.print("  [yellow]DRY RUN — no files will be written[/yellow]")

    # Template variables
    variables = {
        "project_name": args.name,
        "project_slug": args.name.lower().replace(" ", "-").replace("_", "-"),
        "version": "0.1.0",
    }

    # Define all files to create
    files: list[tuple[str, str, str]] = [
        # (relative_path, content_template, description)
        (
            "configs/default.yaml",
            _generate_default_config(args.name),
            "app configuration",
        ),
        (
            "configs/agents/default.yaml",
            _generate_default_agent(),
            "default agent config",
        ),
        (
            ".env.example",
            _generate_env_example(),
            "environment template",
        ),
        (
            "README.md",
            render(
                "# {{project_name}}\n\nPowered by [agentbase](https://github.com/LPK3215/obsidian-notes).\n",
                variables,
            ),
            "project README",
        ),
        (
            ".gitignore",
            _generate_gitignore(),
            "git ignore rules",
        ),
    ]

    # Directory structure
    dirs = [
        "configs/agents",
        "workspace/skills",
        "workspace/memory",
        "workspace/uploads",
        "workspace/outputs",
        "workspace/workspace",
        "data",
        "tests",
    ]

    # Phase 1: Create directories
    for d in dirs:
        dir_path = project_dir / d
        if dir_path.exists():
            if not args.dry_run:
                pass  # Directory already exists
        else:
            if not args.dry_run:
                dir_path.mkdir(parents=True, exist_ok=True)
            console.print(f"  [green]CREATE[/green] {d}/")

    # Phase 2: Check for conflicts
    conflicts: list[str] = []
    for rel_path, content, desc in files:
        file_path = project_dir / rel_path
        if file_path.exists():
            conflicts.append(rel_path)

    if conflicts and not args.force:
        console.print(f"\n  [yellow]WARNING: {len(conflicts)} file(s) already exist:[/yellow]")
        for c in conflicts:
            console.print(f"    [yellow]SKIP[/yellow] {c}")
        console.print(f"  Use --force to overwrite, or --dry-run to preview.")

    # Phase 3: Write files
    created = 0
    skipped = 0
    overwritten = 0
    for rel_path, content, desc in files:
        file_path = project_dir / rel_path

        if file_path.exists():
            if args.force and not args.dry_run:
                file_path.write_text(content, encoding="utf-8")
                console.print(f"  [blue]OVERWRITE[/blue] {rel_path} ({desc})")
                overwritten += 1
            elif args.force and args.dry_run:
                console.print(f"  [blue]WOULD OVERWRITE[/blue] {rel_path} ({desc})")
                overwritten += 1
            else:
                console.print(f"  [yellow]SKIP[/yellow] {rel_path} (already exists)")
                skipped += 1
        else:
            if not args.dry_run:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
            console.print(f"  [green]CREATE[/green] {rel_path} ({desc})")
            created += 1

    # Summary
    console.print(f"\n[{'yellow' if args.dry_run else 'green'}]{'DRY RUN — ' if args.dry_run else ''}Project initialized at {project_dir}[/{'yellow' if args.dry_run else 'green'}]")
    console.print(f"  Created: {created} | Skipped: {skipped} | Overwritten: {overwritten}")
    if not args.dry_run:
        console.print("  Next steps:")
        console.print(f"  1. cd {project_dir}")
        console.print(f"  2. Set your API key in .env")
        console.print(f"  3. agentbase config validate")
        console.print(f"  4. agentbase doctor")
        console.print(f"  5. agentbase serve --reload")
    return 0


def _generate_default_config(name: str) -> str:
    """Generate a comprehensive default.yaml config."""
    return (
        'app:\n'
        f'  name: {name}\n'
        '  env: dev\n'
        '  log_level: INFO\n'
        '  version: "0.4.0"\n'
        '\n'
        'model:\n'
        '  provider: openai\n'
        '  name: deepseek-chat\n'
        '  temperature: 0\n'
        '  timeout_seconds: 120\n'
        '  base_url: https://api.deepseek.com/v1\n'
        '  api_key_env: DEEPSEEK_API_KEY\n'
        '\n'
        'checkpointer:\n'
        '  type: sqlite\n'
        '  options: {}\n'
        '\n'
        'storage:\n'
        '  type: sqlite\n'
        '  db_dir: data\n'
        '\n'
        'embedding:\n'
        '  provider: hash\n'
        '  options: {}\n'
        '\n'
        'web_search:\n'
        '  provider: duckduckgo\n'
        '  options: {}\n'
        '\n'
        'auth:\n'
        '  type: api_key\n'
        '  secret: agentbase-default-secret\n'
        '  token_expiry_hours: 24\n'
        '\n'
        'rate_limit:\n'
        '  enabled: true\n'
        '  max_requests: 60\n'
        '  window_seconds: 60\n'
        '  burst: 10\n'
        '\n'
        'cors:\n'
        '  allow_origins:\n'
        '    - "*"\n'
        '  allow_credentials: true\n'
        '\n'
        'metrics:\n'
        '  enabled: true\n'
        '  path: /metrics\n'
        '  collect_latency: true\n'
        '  collect_agent_metrics: true\n'
        '\n'
        'health_check:\n'
        '  check_storage: true\n'
        '  check_queue: true\n'
        '  check_tracer: false\n'
        '\n'
        'runtime:\n'
        '  default_agent: default\n'
        '  config_dir: configs\n'
        '  workspace_dir: workspace\n'
        '  stream_modes:\n'
        '    - messages\n'
        '    - updates\n'
        '  recursion_limit: 50\n'
        '  max_concurrency: 4\n'
        '\n'
        'extensions:\n'
        '  autodiscover:\n'
        '    - agentbase.extensions.tools\n'
        '    - agentbase.extensions.middleware\n'
        '    - agentbase.extensions.subagents\n'
        '  extra_modules: []\n'
    )


def _generate_default_agent() -> str:
    """Generate a comprehensive default agent YAML."""
    return (
        'name: default\n'
        'description: Default agent with file, memory, and web tools\n'
        'system_prompt: |\n'
        '  You are a helpful assistant. You can use tools to help the user.\n'
        '  Available tools: file operations, memory, knowledge base, web search.\n'
        'tools:\n'
        '  - echo\n'
        '  - get_time\n'
        '  - read_file\n'
        '  - write_file\n'
        '  - list_workspace\n'
        '  - web_search\n'
        '  - memory_save\n'
        '  - memory_get\n'
        '  - memory_list\n'
        '  - memory_search\n'
        '  - kb_search\n'
        'middleware:\n'
        '  - request_logger\n'
        'capabilities:\n'
        '  - file_upload\n'
        '  - files\n'
        'model:\n'
        '  temperature: 0.7\n'
        'metadata:\n'
        '  retry:\n'
        '    max_attempts: 3\n'
        '  summary:\n'
        '    threshold: 20\n'
        '    keep_recent: 6\n'
    )


def _generate_env_example() -> str:
    """Generate .env.example with all supported env vars."""
    return (
        '# Model API keys (first available is used)\n'
        'DEEPSEEK_API_KEY=\n'
        'OPENAI_API_KEY=\n'
        'SILICONFLOW_API_KEY=\n'
        'ANTHROPIC_API_KEY=\n'
        '\n'
        '# API security\n'
        'AGENTBASE_API_KEY=\n'
        'AGENTBASE_CORS_ORIGINS=*\n'
        '\n'
        '# JWT auth (optional, set to enable JWT)\n'
        'AGENTBASE_AUTH__TYPE=api_key\n'
        'AGENTBASE_AUTH__SECRET=agentbase-default-secret\n'
        '\n'
        '# Rate limiting (optional overrides)\n'
        'AGENTBASE_RATE_LIMIT__ENABLED=true\n'
        'AGENTBASE_RATE_LIMIT__MAX_REQUESTS=60\n'
        '\n'
        '# Storage (optional overrides)\n'
        'AGENTBASE_STORAGE__TYPE=sqlite\n'
        'AGENTBASE_STORAGE__DB_DIR=data\n'
        '\n'
        '# Checkpointer (optional overrides)\n'
        'AGENTBASE_CHECKPOINTER__TYPE=sqlite\n'
    )


def _generate_gitignore() -> str:
    """Generate .gitignore for an agentbase project."""
    return (
        '# Python\n'
        '__pycache__/\n'
        '*.pyc\n'
        '*.egg-info/\n'
        '.pytest_cache/\n'
        '.ruff_cache/\n'
        '.mypy_cache/\n'
        'htmlcov/\n'
        '.coverage\n'
        '\n'
        '# Virtual environments\n'
        '.venv/\n'
        'venv/\n'
        '\n'
        '# IDE\n'
        '.idea/\n'
        '.vscode/\n'
        '*.swp\n'
        '\n'
        '# Data files\n'
        'data/*.db\n'
        'data/*.db-journal\n'
        '\n'
        # Environment
        '.env\n'
        '.env.local\n'
        '\n'
        '# Logs\n'
        '*.log\n'
    )


def cmd_config_show(args: argparse.Namespace) -> int:
    """Display the resolved configuration."""
    import yaml
    from agentbase.config.loader import load_app_config

    root = resolve_root_dir(args.root)
    app_config = load_app_config(root)
    data = app_config.model_dump()

    # Remove sensitive fields for display
    if "auth" in data and "secret" in data["auth"]:
        data["auth"]["secret"] = "***hidden***"
    if "auth" in data and "api_key" in data["auth"] and data["auth"]["api_key"]:
        data["auth"]["api_key"] = "***hidden***"

    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    console.print(yaml_str)
    return 0


def cmd_agent_create(args: argparse.Namespace) -> int:
    """Create a new agent configuration file."""
    from pathlib import Path

    from agentbase.config.schema import AgentConfig

    root = resolve_root_dir(args.root)
    agents_dir = root / "configs" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    agent_path = agents_dir / f"{args.name}.yaml"
    if agent_path.exists() and not args.force:
        console.print(f"[yellow]SKIP[/yellow] Agent config already exists: {agent_path}")
        console.print("  Use --force to overwrite.")
        return 1

    agent_config = AgentConfig(
        name=args.name,
        description=args.description or f"Agent: {args.name}",
        system_prompt=args.system_prompt or f"You are a helpful assistant named {args.name}.",
        tools=args.tools.split(",") if args.tools else [],
        middleware=args.middleware.split(",") if args.middleware else [],
        capabilities=args.capabilities.split(",") if args.capabilities else [],
    )

    import yaml
    data = agent_config.model_dump(exclude_none=True)
    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    agent_path.write_text(yaml_str, encoding="utf-8")
    console.print(f"[green]CREATE[/green] {agent_path}")
    console.print(f"  name: {args.name}")
    console.print(f"  tools: {agent_config.tools}")
    console.print(f"  middleware: {agent_config.middleware}")
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    """Start a queue worker process."""
    import time
    from agentbase.core.queue import queue_registry

    root = resolve_root_dir(args.root)
    rt = build_runtime(root)

    queue_provider = rt.app_config.queue.provider
    if queue_provider == "none":
        console.print("[red]ERROR[/red] Queue provider is 'none'. Set queue.provider in config.")
        return 1

    try:
        queue = queue_registry.create(queue_provider, **rt.app_config.queue.options)
    except Exception as exc:
        console.print(f"[red]ERROR[/red] Failed to create queue: {exc}")
        return 1

    console.print(f"[green]Starting queue worker (provider={queue_provider})[/green]")
    console.print(f"  Poll interval: {args.poll_interval}s")

    def handler(task):
        """Process a queued task."""
        agent_name = task.agent_name
        message = task.message
        agent = rt.get_agent(agent_name)
        result = rt.runner.invoke(
            agent=agent,
            agent_name=agent_name,
            message=message,
            thread_id=task.thread_id,
            metadata=task.metadata,
        )
        return {
            "output_text": result.get("output_text", ""),
            "thread_id": result.get("thread_id", ""),
        }

    try:
        while True:
            task = queue.process_one(handler)
            if task is not None:
                status_color = "green" if task.status.value == "completed" else "red"
                console.print(
                    f"  [{status_color}]{task.status.value.upper()}[/{status_color}] "
                    f"task={task.id[:8]}... agent={task.agent_name}"
                )
            else:
                time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker stopped[/yellow]")
        return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Backup database to a file."""
    from pathlib import Path

    from agentbase.bootstrap import build_runtime, resolve_root_dir

    root = resolve_root_dir(args.root)
    rt = build_runtime(root)
    output = args.output or f"backup_{int(time.time())}.{args.format}"
    output_path = Path(output)

    console.print(f"[cyan]Backing up database to {output_path}...[/cyan]")

    storage = rt.factory.storage
    # Detect database type and list tables accordingly
    backend_type = type(storage).__name__
    if hasattr(storage, "db_path"):
        # SQLite
        table_rows = storage.fetchall("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        table_names = [r[0] if not isinstance(r, dict) else r["name"] for r in table_rows]
    elif backend_type == "PostgresBackend":
        table_rows = storage.fetchall("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        table_names = [r[0] if not isinstance(r, dict) else r["tablename"] for r in table_rows]
    elif backend_type == "MySQLBackend":
        table_rows = storage.fetchall("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        table_names = [r[0] if not isinstance(r, dict) else r["table_name"] for r in table_rows]
    else:
        console.print(f"[red]ERROR[/red] Unsupported storage backend: {backend_type}")
        return 1

    if args.format == "json":
        import json
        data = {}
        for table_name in table_names:
            rows = storage.fetchall(f"SELECT * FROM {table_name}")
            data[table_name] = [
                dict(r) if hasattr(r, "keys") else r for r in rows
            ]
        output_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    else:
        # SQL format
        lines = ["-- agentbase database backup", f"-- Created: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
        for table_name in table_names:
            lines.append(f"-- Table: {table_name}")
            rows = storage.fetchall(f"SELECT * FROM {table_name}")
            for r in rows:
                r_dict = dict(r) if hasattr(r, "keys") else {}
                cols = ", ".join(r_dict.keys())
                vals = ", ".join(f"'{v}'" for v in r_dict.values())
                lines.append(f"INSERT INTO {table_name} ({cols}) VALUES ({vals});")
            lines.append("")
        output_path.write_text("\n".join(lines), encoding="utf-8")

    console.print(f"[green]✓ Backup saved to {output_path}[/green]")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore database from a backup file."""
    from pathlib import Path

    from agentbase.bootstrap import build_runtime, resolve_root_dir

    root = resolve_root_dir(args.root)
    rt = build_runtime(root)
    input_path = Path(args.input)

    if not input_path.exists():
        console.print(f"[red]ERROR[/red] File not found: {input_path}")
        return 1

    console.print(f"[cyan]Restoring database from {input_path}...[/cyan]")

    storage = rt.factory.storage
    content = input_path.read_text(encoding="utf-8")

    if args.format == "json":
        import json
        data = json.loads(content)
        total_tables = 0
        skipped_tables = 0
        for table_name, rows in data.items():
            total_tables += 1
            try:
                # Clear existing data first (cross-database compatible)
                storage.execute(f"DELETE FROM {table_name}")
                for row in rows:
                    cols = ", ".join(row.keys())
                    placeholders = ", ".join(["%s"] * len(row))
                    vals = list(row.values())
                    # Convert dicts/lists to JSON strings for proper DB insertion
                    vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in vals]
                    storage.execute(f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})", vals)
                storage.commit()
                console.print(f"  [green]OK[/green] {table_name}: {len(rows)} rows")
            except Exception as exc:
                storage.commit()
                skipped_tables += 1
                console.print(f"  [yellow]SKIP[/yellow] {table_name}: {exc}")
        if skipped_tables:
            console.print(f"[yellow]Restored {total_tables - skipped_tables}/{total_tables} tables ({skipped_tables} skipped — use --format sql for full binary backup)")
        else:
            console.print(f"[green]Restored {total_tables} tables[/green]")
    else:
        # SQL format: execute each statement
        statements = [s.strip() for s in content.split(";") if s.strip() and not s.startswith("--")]
        for stmt in statements:
            storage.execute(stmt)
        storage.commit()
        console.print(f"  [green]OK[/green] {len(statements)} statements executed")

    console.print("[green]✓ Restore complete[/green]")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AgentbaseError as exc:
        console.print(f"[red]ERROR[/red] {exc.code} {exc}")
        return 1
    except KeyboardInterrupt:
        console.print("Interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]ERROR[/red] AGENTBASE_RT_999 {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
