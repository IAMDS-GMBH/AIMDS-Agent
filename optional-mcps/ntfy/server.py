"""ntfy Pub/Sub MCP Server.

Provides tools for push notifications, Cron topic polling, and Inter-Agent messaging
via ntfy (ntfy.sh or self-hosted ntfy instances).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("NtfyMCP")


def _get_ntfy_config() -> Dict[str, str]:
    server_url = os.environ.get("NTFY_SERVER_URL", "https://ntfy.sh").strip().rstrip("/")
    token = os.environ.get("NTFY_AUTH_TOKEN", "").strip()
    default_topic = os.environ.get("NTFY_DEFAULT_TOPIC", "").strip()

    return {
        "server_url": server_url or "https://ntfy.sh",
        "token": token,
        "default_topic": default_topic,
    }


def _get_headers() -> Dict[str, str]:
    cfg = _get_ntfy_config()
    headers = {"User-Agent": "Hermes-Agent-Ntfy/1.0"}
    if cfg["token"]:
        headers["Authorization"] = f"Bearer {cfg['token']}"
    return headers


# ─── Tools ───────────────────────────────────────────────────────────────────


@mcp.tool()
def ntfy_test_connection() -> Dict[str, Any]:
    """Test connectivity to the configured ntfy server."""
    cfg = _get_ntfy_config()
    headers = _get_headers()
    url = f"{cfg['server_url']}/v1/health"

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                return {
                    "ok": True,
                    "server_url": cfg["server_url"],
                    "message": "Successfully connected to ntfy server.",
                }
            # Fallback check for root endpoint
            root_resp = client.get(cfg["server_url"], headers=headers)
            return {
                "ok": root_resp.status_code < 500,
                "server_url": cfg["server_url"],
                "status_code": root_resp.status_code,
                "message": f"ntfy server responded with HTTP {root_resp.status_code}.",
            }
    except Exception as exc:
        return {
            "ok": False,
            "server_url": cfg["server_url"],
            "error": str(exc),
        }


@mcp.tool()
def ntfy_publish_message(
    topic: Optional[str] = None,
    message: str = "",
    title: Optional[str] = None,
    priority: Optional[int] = None,
    tags: Optional[List[str]] = None,
    click_url: Optional[str] = None,
    attach_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish a push notification or message to a ntfy topic."""
    cfg = _get_ntfy_config()
    target_topic = (topic or cfg["default_topic"]).strip()

    if not target_topic:
        raise RuntimeError("Topic name is required (pass topic or set NTFY_DEFAULT_TOPIC environment variable).")

    url = f"{cfg['server_url']}/{target_topic}"
    headers = _get_headers()

    if title:
        headers["Title"] = title
    if priority is not None:
        headers["Priority"] = str(priority)
    if tags:
        headers["Tags"] = ",".join(tags)
    if click_url:
        headers["Click"] = click_url
    if attach_url:
        headers["Attach"] = attach_url

    with httpx.Client(timeout=15.0) as client:
        response = client.post(url, headers=headers, content=message.encode("utf-8"))
        if response.is_error:
            raise RuntimeError(f"ntfy publish error [{response.status_code}]: {response.text}")
        return response.json()


@mcp.tool()
def ntfy_poll_topic(
    topic: Optional[str] = None,
    since: str = "1h",
    limit: int = 20,
) -> Dict[str, Any]:
    """Poll recent messages from a ntfy topic (JSON stream endpoint)."""
    cfg = _get_ntfy_config()
    target_topic = (topic or cfg["default_topic"]).strip()

    if not target_topic:
        raise RuntimeError("Topic name is required (pass topic or set NTFY_DEFAULT_TOPIC environment variable).")

    url = f"{cfg['server_url']}/{target_topic}/json"
    headers = _get_headers()
    params = {"poll": "1", "since": since}

    messages = []
    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, headers=headers, params=params)
        if response.is_error:
            raise RuntimeError(f"ntfy poll error [{response.status_code}]: {response.text}")

        for line in response.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msg_obj = json.loads(line)
                if msg_obj.get("event") == "message":
                    messages.append({
                        "id": msg_obj.get("id"),
                        "time": msg_obj.get("time"),
                        "event": msg_obj.get("event"),
                        "topic": msg_obj.get("topic"),
                        "title": msg_obj.get("title"),
                        "message": msg_obj.get("message"),
                        "priority": msg_obj.get("priority"),
                        "tags": msg_obj.get("tags", []),
                        "click": msg_obj.get("click"),
                        "attachment": msg_obj.get("attachment"),
                    })
            except ValueError:
                continue

    # Return up to limit messages
    return {
        "topic": target_topic,
        "count": len(messages[:limit]),
        "messages": messages[:limit],
    }


if __name__ == "__main__":
    mcp.run()
