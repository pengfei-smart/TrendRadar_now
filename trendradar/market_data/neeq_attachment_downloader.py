# 本文件按NEEQ官方附件任务清单安全下载、校验、缓存并提取文本，不写入数据库或确认法律事实。
"""Resumable, bounded downloader for official NEEQ lifecycle attachments."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from trendradar.market_data.lifecycle_document_text import extract_lifecycle_document_text
from trendradar.market_data.lifecycle_evidence_fetcher import (
    LifecycleEvidenceFetcher,
)
from trendradar.market_data.lifecycle_source_registry import is_allowed_url


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_attachment_name(attachment_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]", "_", str(attachment_id))
    if not value or value in {".", ".."}:
        raise ValueError("附件任务缺少有效 attachment_id")
    return value[:120]


def _reported_kind(task: dict) -> str:
    kinds = sorted({
        str(value).strip().lower()
        for value in task.get("attachment_kinds_reported", [])
        if str(value).strip()
    })
    if len(kinds) != 1 or kinds[0] not in {"pdf", "html"}:
        raise ValueError("附件类型缺失、冲突或不受支持")
    return kinds[0]


def _validate_payload(document, expected_kind: str) -> None:
    body = document.body
    content_type = document.content_type.split(";", 1)[0].strip().lower()
    if expected_kind == "pdf":
        if not body.startswith(b"%PDF-"):
            raise ValueError("附件声明为 PDF，但响应内容不含 PDF 文件魔数")
        if content_type not in {"application/pdf", "application/octet-stream"}:
            raise ValueError(f"PDF 响应 Content-Type 不匹配: {content_type or 'unknown'}")
    else:
        if content_type != "text/html":
            raise ValueError(f"HTML 响应 Content-Type 不匹配: {content_type or 'unknown'}")
        prefix = body[:4096].lstrip().lower()
        if not prefix or not (
            b"<html" in prefix or b"<!doctype html" in prefix or b"<head" in prefix
            or b"<body" in prefix
        ):
            raise ValueError("附件声明为 HTML，但响应内容不像 HTML 文档")


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def _write_json(path: Path, payload: dict) -> None:
    _atomic_write(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def download_neeq_attachments(
    manifest: dict,
    output_dir: str | Path,
    *,
    limit: int = 5,
    offset: int = 0,
    delay_seconds: float = 1.0,
    retries: int = 2,
    fetcher=None,
    extractor: Callable = extract_lifecycle_document_text,
    sleep: Callable = time.sleep,
) -> dict:
    """Download at most limit tasks sequentially and persist resumable metadata."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("tasks"), list):
        raise ValueError("附件清单必须包含 tasks 数组")
    if limit < 1:
        raise ValueError("limit 必须大于等于 1")
    if offset < 0:
        raise ValueError("offset 不能小于 0")
    if delay_seconds < 0:
        raise ValueError("delay_seconds 不能小于 0")
    if retries < 0 or retries > 5:
        raise ValueError("retries 必须在 0 到 5 之间")

    tasks = manifest["tasks"][offset:offset + limit]
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("附件任务格式无效")
        url = str(task.get("url") or "")
        if not is_allowed_url("neeq", url):
            raise ValueError("附件任务 URL 不在 NEEQ 官方 HTTPS 白名单内")
        _reported_kind(task)
        _safe_attachment_name(task.get("attachment_id", ""))

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    state_path = destination / "neeq_attachment_download_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("results"), dict):
            raise ValueError("已有下载状态文件结构无效，拒绝覆盖")
    else:
        state = {
            "schema_version": 1,
            "source": "neeq_official_termination_result_list",
            "generated_at": _utc_now(),
            "results": {},
            "safety": {
                "database_writes": 0,
                "formal_lifecycle_writes": 0,
                "legal_fact_confirmation": 0,
                "sequential_downloads": True,
            },
        }

    client = fetcher or LifecycleEvidenceFetcher()
    processed = 0
    for task in tasks:
        attachment_id = _safe_attachment_name(task["attachment_id"])
        url = str(task["url"])
        kind = _reported_kind(task)
        extension = ".pdf" if kind == "pdf" else ".html"
        binary_path = destination / (attachment_id + extension)
        text_path = destination / (attachment_id + ".txt")
        prior = state["results"].get(attachment_id, {})
        if prior.get("download_status") == "downloaded" and binary_path.exists():
            existing_hash = hashlib.sha256(binary_path.read_bytes()).hexdigest()
            if existing_hash == prior.get("content_sha256") and prior.get("requested_url") == url:
                processed += 1
                continue

        last_error = None
        for attempt in range(retries + 1):
            try:
                document = client.fetch("neeq", url)
                _validate_payload(document, kind)
                content_hash = hashlib.sha256(document.body).hexdigest()
                _atomic_write(binary_path, document.body)
                extraction_status = "extracted"
                extraction_error = None
                try:
                    extracted_text = extractor(document)
                    _atomic_write(text_path, extracted_text.encode("utf-8"))
                except Exception as exc:
                    extraction_status = "needs_manual_review"
                    extraction_error = str(exc)
                    if text_path.exists():
                        text_path.unlink()
                state["results"][attachment_id] = {
                    "attachment_id": attachment_id,
                    "requested_url": document.requested_url,
                    "final_url": document.final_url,
                    "status_code": document.status_code,
                    "content_type": document.content_type,
                    "reported_kind": kind,
                    "download_status": "downloaded",
                    "text_extraction_status": extraction_status,
                    "text_extraction_error": extraction_error,
                    "binary_file": binary_path.name,
                    "text_file": text_path.name if extraction_status == "extracted" else None,
                    "content_bytes": len(document.body),
                    "content_sha256": content_hash,
                    "retrieved_at": _utc_now(),
                    "attempt_count": attempt + 1,
                    "legal_fact_confirmed": False,
                    "formal_lifecycle_write_allowed": False,
                }
                last_error = None
                break
            except Exception as exc:
                last_error = str(exc)
                if attempt < retries:
                    sleep(delay_seconds * (attempt + 1))

        if last_error is not None:
            state["results"][attachment_id] = {
                "attachment_id": attachment_id,
                "requested_url": url,
                "reported_kind": kind,
                "download_status": "failed",
                "text_extraction_status": "not_attempted",
                "error": last_error,
                "attempt_count": retries + 1,
                "retrieved_at": _utc_now(),
                "legal_fact_confirmed": False,
                "formal_lifecycle_write_allowed": False,
            }
        processed += 1
        _write_json(state_path, state)
        if delay_seconds and processed < len(tasks):
            sleep(delay_seconds)

    _write_json(state_path, state)
    results = list(state["results"].values())
    return {
        "processed_this_run": processed,
        "requested_limit": limit,
        "requested_offset": offset,
        "downloaded_total": sum(item.get("download_status") == "downloaded" for item in results),
        "failed_total": sum(item.get("download_status") == "failed" for item in results),
        "text_extracted_total": sum(item.get("text_extraction_status") == "extracted" for item in results),
        "needs_manual_review_total": sum(
            item.get("text_extraction_status") == "needs_manual_review" for item in results
        ),
        "state_file": str(state_path),
        "safety": state["safety"],
    }
