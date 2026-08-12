"""Knowledge base tools — expose KnowledgeBase CRUD to agents.

Tools provided:
- ``kb_add``           — add a document to the knowledge base
- ``kb_get``           — get a document by ID
- ``kb_list``          — list all documents
- ``kb_search``        — search documents by text
- ``kb_update``        — update a document's title, content, or metadata
- ``kb_delete``        — delete a document by ID
- ``kb_ingest``        — ingest a file from workspace into the knowledge base
- ``kb_batch_ingest``  — ingest all files in a workspace directory
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from agentbase.core.knowledge import KnowledgeBase
from agentbase.extensions._meta import ExtensionMeta
from agentbase.extensions.tools._workspace import resolve_within_workspace
from agentbase.registry.tools import register_tool


def _get_kb(context: dict[str, Any] | None) -> KnowledgeBase:
    kb = (context or {}).get("knowledge_base")
    if kb is None:
        raise RuntimeError("knowledge_base not available in context")
    return kb


@register_tool("kb_add", meta=ExtensionMeta(
    name="kb_add", kind="tool", description="Add a document to the knowledge base.", requires_context=["knowledge_base"]
))
def build_kb_add_tool(context: dict[str, Any] | None = None):
    kb = _get_kb(context)

    @tool
    def kb_add(source: str, title: str, content: str, metadata: str = "") -> str:
        """Add a document. ``metadata`` is an optional JSON string."""
        meta_dict = {}
        if metadata:
            try:
                meta_dict = json.loads(metadata)
            except json.JSONDecodeError:
                meta_dict = {"raw": metadata}
        doc = kb.add_document(source=source, title=title, content=content, metadata=meta_dict)
        return f"Added document: id={doc.id} title={doc.title} chunks={doc.chunk_count}"

    return kb_add


@register_tool("kb_get", meta=ExtensionMeta(
    name="kb_get", kind="tool", description="Get a document by ID.", requires_context=["knowledge_base"]
))
def build_kb_get_tool(context: dict[str, Any] | None = None):
    kb = _get_kb(context)

    @tool
    def kb_get(doc_id: int) -> str:
        """Retrieve a document by its ID."""
        doc = kb.get_document(doc_id=doc_id)
        if doc is None:
            return f"Document not found: id={doc_id}"
        return json.dumps(doc.to_dict(), ensure_ascii=False)

    return kb_get


@register_tool("kb_list", meta=ExtensionMeta(
    name="kb_list", kind="tool", description="List all documents in the knowledge base.", requires_context=["knowledge_base"]
))
def build_kb_list_tool(context: dict[str, Any] | None = None):
    kb = _get_kb(context)

    @tool
    def kb_list() -> str:
        """List all documents."""
        docs = kb.list_documents()
        if not docs:
            return "<knowledge base is empty>"
        lines = [f"- [{d.id}] {d.title} (source={d.source}, chunks={d.chunk_count})" for d in docs]
        return "\n".join(lines)

    return kb_list


@register_tool("kb_search", meta=ExtensionMeta(
    name="kb_search", kind="tool", description="Search documents by text query.", requires_context=["knowledge_base"]
))
def build_kb_search_tool(context: dict[str, Any] | None = None):
    kb = _get_kb(context)

    @tool
    def kb_search(query: str, top_k: int = 5) -> str:
        """Search the knowledge base. Returns top_k matching chunks."""
        results = kb.search(query, top_k=top_k)
        if not results:
            return f"<no results for '{query}'>"
        lines: list[str] = []
        for r in results:
            doc = r.document
            chunk_preview = r.chunk.content[:200] if r.chunk else ""
            lines.append(f"- [{doc.id}] {doc.title} (score={r.score:.2f})\n  {chunk_preview}...")
        return "\n".join(lines)

    return kb_search


@register_tool("kb_delete", meta=ExtensionMeta(
    name="kb_delete", kind="tool", description="Delete a document by ID.", requires_context=["knowledge_base"]
))
def build_kb_delete_tool(context: dict[str, Any] | None = None):
    kb = _get_kb(context)

    @tool
    def kb_delete(doc_id: int) -> str:
        """Delete a document by ID."""
        if kb.delete_document(doc_id=doc_id):
            return f"Deleted document: id={doc_id}"
        return f"Document not found: id={doc_id}"

    return kb_delete


@register_tool("kb_ingest", meta=ExtensionMeta(
    name="kb_ingest", kind="tool", description="Ingest a file from workspace into the knowledge base.", requires_context=["knowledge_base", "workspace_dir"]
))
def build_kb_ingest_tool(context: dict[str, Any] | None = None):
    ctx = context or {}
    kb = _get_kb(ctx)
    workspace = ctx.get("workspace_dir")
    if workspace is None:
        root_dir = ctx.get("root_dir")
        from pathlib import Path
        workspace = Path(root_dir) / "workspace" if root_dir else Path("workspace")

    @tool
    def kb_ingest(path: str, title: str = "") -> str:
        """Read a file from workspace and add it to the knowledge base."""
        from pathlib import Path
        try:
            file_path = resolve_within_workspace(Path(workspace), path)
        except ValueError as exc:
            return str(exc)
        if not file_path.exists():
            return f"File not found: {path}"
        doc = kb.ingest_file(file_path, title=title or None)
        return f"Ingested: id={doc.id} title={doc.title} chunks={doc.chunk_count}"

    return kb_ingest


@register_tool("kb_update", meta=ExtensionMeta(
    name="kb_update", kind="tool", description="Update a document's title, content, or metadata.", requires_context=["knowledge_base"]
))
def build_kb_update_tool(context: dict[str, Any] | None = None):
    kb = _get_kb(context)

    @tool
    def kb_update(doc_id: int, title: str = "", content: str = "", metadata: str = "") -> str:
        """Update a document. Pass empty string to skip a field.
        If content is provided, chunks are rebuilt.
        metadata is an optional JSON string (replaces existing metadata)."""
        kwargs: dict[str, Any] = {}
        if title:
            kwargs["title"] = title
        if content:
            kwargs["content"] = content
        if metadata:
            try:
                kwargs["metadata"] = json.loads(metadata)
            except json.JSONDecodeError:
                kwargs["metadata"] = {"raw": metadata}
        if not kwargs:
            return "Nothing to update: provide at least one of title, content, metadata"
        doc = kb.update_document(doc_id=doc_id, **kwargs)
        if doc is None:
            return f"Document not found: id={doc_id}"
        return f"Updated: id={doc.id} title={doc.title} chunks={doc.chunk_count}"

    return kb_update


@register_tool("kb_batch_ingest", meta=ExtensionMeta(
    name="kb_batch_ingest", kind="tool", description="Ingest all files in a workspace directory into the knowledge base.", requires_context=["knowledge_base", "workspace_dir"]
))
def build_kb_batch_ingest_tool(context: dict[str, Any] | None = None):
    ctx = context or {}
    kb = _get_kb(ctx)
    workspace = ctx.get("workspace_dir")
    if workspace is None:
        root_dir = ctx.get("root_dir")
        from pathlib import Path
        workspace = Path(root_dir) / "workspace" if root_dir else Path("workspace")

    @tool
    def kb_batch_ingest(directory: str, pattern: str = "**/*") -> str:
        """Ingest all matching files from a workspace directory.

        Args:
            directory: Directory path within workspace.
            pattern: Glob pattern (default '**/*' = all files recursively).

        Returns:
            Summary of ingested files.
        """
        from pathlib import Path
        try:
            dir_path = resolve_within_workspace(Path(workspace), directory)
        except ValueError as exc:
            return str(exc)
        if not dir_path.is_dir():
            return f"Directory not found: {directory}"

        files = sorted(dir_path.glob(pattern))
        files = [f for f in files if f.is_file()]
        if not files:
            return f"No files found in {directory} matching {pattern}"

        success: list[str] = []
        errors: list[str] = []
        for f in files:
            try:
                doc = kb.ingest_file(f)
                success.append(f"  - id={doc.id} {f.name} ({doc.chunk_count} chunks)")
            except Exception as exc:
                errors.append(f"  - {f.name}: {exc}")

        lines = [f"Ingested {len(success)}/{len(files)} files:"]
        lines.extend(success)
        if errors:
            lines.append(f"Errors ({len(errors)}):")
            lines.extend(errors)
        return "\n".join(lines)

    return kb_batch_ingest
