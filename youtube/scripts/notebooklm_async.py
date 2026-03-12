#!/usr/bin/env python3
"""Shared async NotebookLM helpers for the youtube skill."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from notebooklm import NotebookLMClient

DEFAULT_OUTPUT_ROOT = Path("/root/.openclaw/workspace/notebooklm-library/notebooklm")
NOTEBOOK_URL = "https://notebooklm.google.com/notebook/{id}"


def normalize_artifact_type(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def resolve_artifact_types(artifact_types: list[str]) -> list[str]:
    resolved = []
    seen = set()
    for artifact_type in artifact_types:
        normalized = normalize_artifact_type(artifact_type)
        if normalized in ARTIFACT_SPECS and normalized not in seen:
            resolved.append(normalized)
            seen.add(normalized)
    return resolved


def sanitize_path_component(value: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return (cleaned or "unknown")[:limit]


ARTIFACT_SPECS = {
    "report": {
        "generate": lambda client, notebook_id, source_ids, language, title: client.artifacts.generate_report(
            notebook_id,
            source_ids=source_ids,
            language=language,
        ),
        "download": lambda client, notebook_id, artifact_id, path: client.artifacts.download_report(
            notebook_id,
            str(path),
            artifact_id=artifact_id,
        ),
        "ext": "md",
        "wait_timeout": 300,
    },
    "audio": {
        "generate": lambda client, notebook_id, source_ids, language, title: client.artifacts.generate_audio(
            notebook_id,
            source_ids=source_ids,
            language=language,
        ),
        "download": lambda client, notebook_id, artifact_id, path: client.artifacts.download_audio(
            notebook_id,
            str(path),
            artifact_id=artifact_id,
        ),
        "ext": "mp4",
        "wait_timeout": 600,
    },
    "video": {
        "generate": lambda client, notebook_id, source_ids, language, title: client.artifacts.generate_video(
            notebook_id,
            source_ids=source_ids,
            language=language,
        ),
        "download": lambda client, notebook_id, artifact_id, path: client.artifacts.download_video(
            notebook_id,
            str(path),
            artifact_id=artifact_id,
        ),
        "ext": "mp4",
        "wait_timeout": 600,
    },
    "quiz": {
        "generate": lambda client, notebook_id, source_ids, language, title: client.artifacts.generate_quiz(
            notebook_id,
            source_ids=source_ids,
        ),
        "download": lambda client, notebook_id, artifact_id, path: client.artifacts.download_quiz(
            notebook_id,
            str(path),
            artifact_id=artifact_id,
            output_format="markdown",
        ),
        "ext": "md",
        "wait_timeout": 300,
    },
    "slide_deck": {
        "generate": lambda client, notebook_id, source_ids, language, title: client.artifacts.generate_slide_deck(
            notebook_id,
            source_ids=source_ids,
            language=language,
        ),
        "download": lambda client, notebook_id, artifact_id, path: client.artifacts.download_slide_deck(
            notebook_id,
            str(path),
            artifact_id=artifact_id,
        ),
        "ext": "pdf",
        "wait_timeout": 600,
    },
    "mind_map": {
        "generate": lambda client, notebook_id, source_ids, language, title: client.artifacts.generate_mind_map(
            notebook_id,
            source_ids=source_ids,
        ),
        "download": lambda client, notebook_id, artifact_id, path: client.artifacts.download_mind_map(
            notebook_id,
            str(path),
            artifact_id=artifact_id,
        ),
        "ext": "json",
        "wait_timeout": 300,
        "synchronous": True,
    },
    "infographic": {
        "generate": lambda client, notebook_id, source_ids, language, title: client.artifacts.generate_infographic(
            notebook_id,
            source_ids=source_ids,
            language=language,
        ),
        "download": lambda client, notebook_id, artifact_id, path: client.artifacts.download_infographic(
            notebook_id,
            str(path),
            artifact_id=artifact_id,
        ),
        "ext": "png",
        "wait_timeout": 600,
    },
    "flashcards": {
        "generate": lambda client, notebook_id, source_ids, language, title: client.artifacts.generate_flashcards(
            notebook_id,
            source_ids=source_ids,
        ),
        "download": lambda client, notebook_id, artifact_id, path: client.artifacts.download_flashcards(
            notebook_id,
            str(path),
            artifact_id=artifact_id,
            output_format="markdown",
        ),
        "ext": "md",
        "wait_timeout": 300,
    },
    "data_table": {
        "generate": lambda client, notebook_id, source_ids, language, title: client.artifacts.generate_data_table(
            notebook_id,
            source_ids=source_ids,
            language=language,
            instructions=f"Key data and statistics from: {title}",
        ),
        "download": lambda client, notebook_id, artifact_id, path: client.artifacts.download_data_table(
            notebook_id,
            str(path),
            artifact_id=artifact_id,
        ),
        "ext": "csv",
        "wait_timeout": 300,
    },
}


async def get_or_create_notebook(
    client: NotebookLMClient,
    channel_name: str,
    language: str = "zh_Hans",
    existing_notebook_id: str | None = None,
) -> str | None:
    notebooks = await client.notebooks.list()

    if existing_notebook_id:
        for nb in notebooks:
            if nb.id == existing_notebook_id:
                return nb.id

    for nb in notebooks:
        title = nb.title or ""
        if title == channel_name or title == f"📺 {channel_name}" or channel_name in title:
            return nb.id

    nb = await client.notebooks.create(f"📺 {channel_name}")
    try:
        await client.settings.set_language(nb.id, language)
    except Exception:
        pass
    return nb.id


async def add_source(client: NotebookLMClient, notebook_id: str, url: str):
    source = await client.sources.add_url(notebook_id, url)
    return await client.sources.wait_until_ready(notebook_id, source.id)


async def delete_notebook(client: NotebookLMClient, notebook_id: str) -> bool:
    try:
        return await client.notebooks.delete(notebook_id)
    except Exception:
        return False


async def generate_artifact(
    client: NotebookLMClient,
    notebook_id: str,
    artifact_type: str,
    output_dir: Path,
    source_ids: list[str] | None,
    language: str,
    title: str,
    wait: bool = True,
) -> dict:
    normalized = normalize_artifact_type(artifact_type)
    spec = ARTIFACT_SPECS.get(normalized)
    result = {
        "name": artifact_type,
        "normalized": normalized,
        "status": "failed",
        "path": None,
        "error": None,
        "task_id": None,
    }

    if not spec:
        result["error"] = f"unsupported artifact type: {artifact_type}"
        return result

    try:
        generation = await spec["generate"](client, notebook_id, source_ids, language, title)
        task_id = getattr(generation, "task_id", None)
        result["task_id"] = task_id

        if wait and not spec.get("synchronous"):
            final = await client.artifacts.wait_for_completion(
                notebook_id,
                task_id,
                timeout=spec["wait_timeout"],
                initial_interval=5,
            )
            if not final.is_complete:
                result["error"] = f"generation not complete: {final.status}"
                return result

        if not wait and not spec.get("synchronous"):
            result["status"] = "pending"
            return result

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{normalized}.{spec['ext']}"
        downloaded_path = await spec["download"](client, notebook_id, task_id, output_path)
        result["status"] = "ok"
        result["path"] = str(downloaded_path)
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


async def generate_artifacts(
    client: NotebookLMClient,
    notebook_id: str,
    artifact_types: list[str],
    output_dir: Path,
    source_ids: list[str] | None,
    language: str,
    title: str,
    wait: bool = True,
) -> dict:
    tasks = [
        generate_artifact(
            client,
            notebook_id,
            artifact_type,
            output_dir,
            source_ids,
            language,
            title,
            wait=wait,
        )
        for artifact_type in artifact_types
    ]
    rows = await asyncio.gather(*tasks)

    artifacts = {}
    downloaded = {}
    errors = []
    for row in rows:
        artifacts[row["name"]] = row["status"]
        if row["path"]:
            downloaded[row["normalized"]] = row["path"]
        if row["error"]:
            errors.append(f"{row['name']}: {row['error']}")

    return {
        "artifacts": artifacts,
        "downloaded": downloaded,
        "errors": errors,
    }
