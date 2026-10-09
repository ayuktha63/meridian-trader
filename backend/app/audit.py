"""Append-only audit trail (see `db.models.AuditRow`).

Pass the caller's session when there is one: the entry then commits with the
action it describes, and a second connection never has to wait on the
caller's own write lock. Never raises — failing to write the log must not be
what stops a protective action.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("meridian.audit")


async def record(event: str, subject: Optional[str] = None,
                 session: Optional[AsyncSession] = None, **detail: Any) -> None:
    from .db.models import AuditRow
    from .db.session import session_scope
    try:
        row = AuditRow(event=event, subject=subject, detail=detail)
        if session is not None:
            session.add(row)
        else:
            async with session_scope() as own:
                own.add(row)
    except Exception:  # noqa: BLE001
        log.exception("audit write failed: %s %s %s", event, subject, detail)


async def recent(limit: int = 200) -> List[dict]:
    from .db.models import AuditRow
    from .db.session import session_scope
    async with session_scope() as session:
        rows = (await session.execute(
            select(AuditRow).order_by(AuditRow.at.desc()).limit(limit))).scalars().all()
        return [{"id": r.id, "at": r.at, "event": r.event, "subject": r.subject,
                 "detail": r.detail} for r in rows]
