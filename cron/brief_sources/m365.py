"""Microsoft 365 adapter: calendar, unread mail, To Do, Teams activity (AIS-305).

Prefers the bundled ``m365_brief_snapshot`` tool of our own MSOffice365MCP
server (one call, server-side trimmed); falls back to the four individual
tools. Never keeps mail bodies or attendee lists.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from cron.brief_sources.base import (
    BriefItem, SourceAdapter, SourceContext, SourceStatus, Window, clip, dispatch_json,
    first_list, iso_day, parse_dt, resolve_tool, strip_html, to_local,
)

SERVER = "MSOffice365MCP"
SNAPSHOT = "m365_brief_snapshot"
EVENTS, MAILS, TODOS, FEED = "m365_get_events", "m365_list_emails", "m365_list_todo_tasks", "m365_get_activity_feed"


class M365Adapter(SourceAdapter):
    name = "m365"
    server = SERVER
    required_tools = ()  # checked per sub-source in fetch()

    def availability(self, ctx: SourceContext) -> Optional[SourceStatus]:
        base = super().availability(ctx)
        if base is not None:
            return base
        have_any = any(resolve_tool(ctx.tool_names, SERVER, t) for t in (SNAPSHOT, EVENTS, MAILS, TODOS, FEED))
        if not have_any:
            return SourceStatus(self.name, "skipped", "no brief tools in tools.include")
        return None

    # ------------------------------------------------------------------
    def fetch(self, window: Window, ctx: SourceContext) -> List[BriefItem]:
        cfg = (ctx.cfg.get("cron") or {}).get("brief_collector") or {}
        mail_top = int(cfg.get("mail_top", 15) or 15)
        start_iso = window.start.strftime("%Y-%m-%dT%H:%M:%S")
        end_iso = window.end.strftime("%Y-%m-%dT%H:%M:%S")
        want_mail = window.kind in ("morning-brief", "weekly-review", "mail-check")
        want_teams = window.kind in ("morning-brief", "weekly-review", "teams-check")
        want_cal = window.kind in ("morning-brief", "weekly-review")
        items: List[BriefItem] = []
        errors: List[str] = []

        snap = resolve_tool(ctx.tool_names, SERVER, SNAPSHOT)
        if snap:
            data = dispatch_json(ctx, snap, {
                "start_time_iso": start_iso, "end_time_iso": end_iso,
                "mail_top": mail_top if want_mail else 0, "todo_top": 10 if want_cal else 0,
                "chats_top": 5 if want_teams else 0,
            })
            if want_cal:
                items += self._events(first_list(data, "events"), window)
                items += self._todos(data.get("todos") if isinstance(data, dict) else None, window)
            if want_mail:
                items += self._mails(first_list(data, "unread_mail", "mails", "emails"), window, mail_top)
            if want_teams:
                items += self._teams((data.get("teams") if isinstance(data, dict) else None) or data, window)
            for err in (data.get("errors") or []) if isinstance(data, dict) else []:
                errors.append(f"{err.get('source')}: {clip(err.get('error'), 60)}")
        else:
            if want_cal:
                self._try(errors, "events", lambda: items.extend(self._events(first_list(
                    dispatch_json(ctx, self._tool(ctx, EVENTS), {"start_time_iso": start_iso, "end_time_iso": end_iso, "top": 20}),
                    "value", "events"), window)))
                self._try(errors, "todos", lambda: items.extend(self._todos(
                    dispatch_json(ctx, self._tool(ctx, TODOS), {"top": 20}), window)))
            if want_mail:
                self._try(errors, "mail", lambda: items.extend(self._mails(first_list(
                    dispatch_json(ctx, self._tool(ctx, MAILS), {"top": max(mail_top, 25)}), "value", "emails", "messages"),
                    window, mail_top)))
            if want_teams:
                self._try(errors, "teams", lambda: items.extend(self._teams(
                    dispatch_json(ctx, self._tool(ctx, FEED), {"top_chats": 5, "top_messages_per_chat": 2}), window)))
        if errors:
            self.partial_errors = errors  # surfaced by the collector in the sources line
        return items

    @staticmethod
    def _tool(ctx: SourceContext, suffix: str) -> str:
        name = resolve_tool(ctx.tool_names, SERVER, suffix)
        if not name:
            raise RuntimeError(f"{suffix} not in tools.include")
        return name

    @staticmethod
    def _try(errors: List[str], label: str, fn) -> None:
        try:
            fn()
        except Exception as exc:
            errors.append(f"{label}: {clip(exc, 60)}")

    # ------------------------------------------------------------------
    def _events(self, raw: List[Any], window: Window) -> List[BriefItem]:
        out: List[BriefItem] = []
        for ev in raw:
            if not isinstance(ev, dict):
                continue
            # Graph's {"dateTime","timeZone"} is the authoritative shape; the
            # server-added *_local strings are a display fallback.
            start = to_local(parse_dt(ev.get("start") or ev.get("start_local"), window.tz), window.tz)
            end = to_local(parse_dt(ev.get("end") or ev.get("end_local"), window.tz), window.tz)
            if start is None or not window.contains_day(start.date()):
                continue
            organizer = ev.get("organizer")
            if isinstance(organizer, dict):
                organizer = ((organizer.get("emailAddress") or {}).get("name")) or organizer.get("name") or ""
            location = ev.get("location")
            if isinstance(location, dict):
                location = location.get("displayName") or ""
            all_day = bool(ev.get("is_all_day") or ev.get("isAllDay"))
            key = str(ev.get("id") or f"{iso_day(start)}T{start.strftime('%H%M')}-{clip(ev.get('subject'), 40)}")
            out.append(BriefItem(
                source="m365", kind="event", key=key, title=clip(ev.get("subject"), 90),
                status=str(ev.get("response_status") or ev.get("responseStatus", {}).get("response", "") if isinstance(ev.get("responseStatus"), dict) else ev.get("response_status") or ""),
                starts_at=start.isoformat(timespec="minutes"), ends_at=end.isoformat(timespec="minutes") if end else "",
                extra={"all_day": all_day, "location": clip(location, 50), "organizer": clip(organizer, 40)},
            ))
        return out

    def _mails(self, raw: List[Any], window: Window, top: int) -> List[BriefItem]:
        out: List[BriefItem] = []
        for m in raw:
            if not isinstance(m, dict):
                continue
            if m.get("isRead") is True or m.get("is_read") is True:
                continue
            received = to_local(parse_dt(m.get("received") or m.get("receivedDateTime"), window.tz), window.tz)
            sender = m.get("from_name") or m.get("from")
            if isinstance(sender, dict):
                sender = ((sender.get("emailAddress") or {}).get("name")) or ((sender.get("emailAddress") or {}).get("address")) or ""
            out.append(BriefItem(
                source="m365", kind="mail", key=str(m.get("id") or f"{iso_day(received)}-{clip(m.get('subject'), 40)}"),
                title=clip(m.get("subject"), 90), status="unread",
                priority=str(m.get("importance") or ""),
                updated_at=received.isoformat(timespec="minutes") if received else "",
                url=str(m.get("web_link") or m.get("webLink") or ""),
                extra={"from": clip(sender, 40), "has_attachments": bool(m.get("has_attachments") or m.get("hasAttachments"))},
            ))
            if len(out) >= top:
                break
        return out

    def _todos(self, raw: Any, window: Window) -> List[BriefItem]:
        tasks = first_list(raw, "todos", "tasks", "value") if raw is not None else []
        out: List[BriefItem] = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            status = str(t.get("status") or "")
            if status.lower() == "completed":
                continue
            due = parse_dt(t.get("due") or t.get("dueDateTime"), window.tz)
            out.append(BriefItem(
                source="m365", kind="task", key=str(t.get("id") or clip(t.get("title"), 60)),
                title=clip(t.get("title"), 90), status=status or "open", priority=str(t.get("importance") or ""),
                due_at=iso_day(due), extra={"list": clip(t.get("list") or t.get("list_name"), 30)},
            ))
        # due inside window first, then undated, then later
        out.sort(key=lambda i: (0 if window.contains_day(parse_dt(i.due_at, window.tz).date() if i.due_at else None) else (1 if not i.due_at else 2), i.due_at))
        return out[:10]

    def _teams(self, raw: Any, window: Window) -> List[BriefItem]:
        out: List[BriefItem] = []
        if not isinstance(raw, dict):
            return out
        chats = raw.get("chats") or raw.get("recent_chats") or []
        channels = raw.get("channels") or raw.get("team_channels") or []
        for group, label in ((chats, "chat"), (channels, "channel")):
            for c in group[:8]:
                if not isinstance(c, dict):
                    continue
                topic = c.get("topic") or c.get("channel") or c.get("name") or ""
                chat_id = c.get("chat_id") or c.get("id") or clip(topic, 30)
                for msg in (c.get("messages") or c.get("recent_messages") or [])[:3]:
                    if not isinstance(msg, dict):
                        continue
                    created = to_local(parse_dt(msg.get("created") or msg.get("created_at") or msg.get("createdDateTime"), window.tz), window.tz)
                    sender = msg.get("from") or msg.get("from_name") or ""
                    if isinstance(sender, dict):
                        sender = ((sender.get("user") or {}).get("displayName")) or sender.get("name") or ""
                    key = f"{chat_id}:{created.isoformat(timespec='minutes') if created else ''}:{clip(sender, 20)}"
                    out.append(BriefItem(
                        source="m365", kind="chat", key=key,
                        title=clip(strip_html(msg.get("preview") or msg.get("body_preview") or msg.get("body")), 120),
                        status="unread", updated_at=created.isoformat(timespec="minutes") if created else "",
                        extra={"topic": clip(topic, 40), "from": clip(sender, 30), "scope": label},
                    ))
        return out
