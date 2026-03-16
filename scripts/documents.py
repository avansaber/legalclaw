"""LegalClaw -- document management domain module

Actions for legal documents, versioning, filing (1 table, 10 actions).
Imported by db_query.py (unified router).
"""
import os
import sys
import uuid
from datetime import datetime, timezone

try:
    sys.path.insert(0, os.path.expanduser("~/.openclaw/erpclaw/lib"))
    from erpclaw_lib.db import get_connection
    from erpclaw_lib.naming import get_next_name, ENTITY_PREFIXES
    from erpclaw_lib.response import ok, err, row_to_dict
    from erpclaw_lib.audit import audit
    from erpclaw_lib.query import (
        Q, P, Table, Field, fn, Order, LiteralValue,
        insert_row, update_row, dynamic_update,
    )

    ENTITY_PREFIXES.setdefault("legalclaw_document", "LDOC-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_DOCUMENT_TYPES = (
    "pleading", "motion", "brief", "contract", "correspondence",
    "discovery", "evidence", "order", "general", "other",
)
VALID_DOCUMENT_STATUSES = ("draft", "review", "final", "filed", "archived")

# ── Table aliases ──
_company = Table("company")
_matter = Table("legalclaw_matter")
_doc = Table("legalclaw_document")
_audit = Table("audit_log")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    q = Q.from_(_company).select(_company.id).where(_company.id == P())
    if not conn.execute(q.get_sql(), (company_id,)).fetchone():
        err(f"Company {company_id} not found")


def _validate_document(conn, doc_id):
    if not doc_id:
        err("--document-id is required")
    q = Q.from_(_doc).select(_doc.star).where(_doc.id == P())
    row = conn.execute(q.get_sql(), (doc_id,)).fetchone()
    if not row:
        err(f"Document {doc_id} not found")
    return row


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


# ---------------------------------------------------------------------------
# 1. add-legal-document
# ---------------------------------------------------------------------------
def add_legal_document(conn, args):
    _validate_company(conn, args.company_id)

    title = getattr(args, "doc_title", None)
    if not title:
        err("--doc-title is required")

    document_type = getattr(args, "document_type", None) or "general"
    _validate_enum(document_type, VALID_DOCUMENT_TYPES, "document-type")

    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        q = Q.from_(_matter).select(_matter.id).where(_matter.id == P())
        if not conn.execute(q.get_sql(), (matter_id,)).fetchone():
            err(f"Matter {matter_id} not found")

    doc_id = str(uuid.uuid4())
    ns = get_next_name(conn, "legalclaw_document", company_id=args.company_id)
    now = _now_iso()

    sql, _ = insert_row("legalclaw_document", {"id": P(), "naming_series": P(), "matter_id": P(), "title": P(), "document_type": P(), "file_name": P(), "content": P(), "version": P(), "status": P(), "filed_date": P(), "court_reference": P(), "company_id": P(), "created_at": P(), "updated_at": P()})
    conn.execute(sql, (
        doc_id, ns, matter_id, title, document_type,
        getattr(args, "file_name", None),
        getattr(args, "content", None),
        "1", "draft", None,
        getattr(args, "court_reference", None),
        args.company_id, now, now,
    ))
    audit(conn, "legalclaw_document", doc_id, "legal-add-legal-document", args.company_id)
    conn.commit()
    ok({
        "id": doc_id, "naming_series": ns, "title": title,
        "document_type": document_type, "version": "1", "document_status": "draft",
    })


# ---------------------------------------------------------------------------
# 2. update-legal-document
# ---------------------------------------------------------------------------
def update_legal_document(conn, args):
    doc_id = getattr(args, "document_id", None)
    doc = _validate_document(conn, doc_id)

    if doc["status"] == "archived":
        err(f"Document {doc_id} is archived and cannot be modified")

    data = {}
    changed = []
    for arg_name, col_name in {
        "doc_title": "title", "document_type": "document_type",
        "file_name": "file_name", "content": "content",
        "court_reference": "court_reference",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            if arg_name == "document_type":
                _validate_enum(val, VALID_DOCUMENT_TYPES, "document-type")
            data[col_name] = val
            changed.append(col_name)

    document_status = getattr(args, "document_status", None)
    if document_status:
        _validate_enum(document_status, VALID_DOCUMENT_STATUSES, "document-status")
        data["status"] = document_status
        changed.append("status")

    if not data:
        err("No fields to update")

    data["updated_at"] = _now_iso()
    sql, params = dynamic_update("legalclaw_document", data, where={"id": doc_id})
    conn.execute(sql, params)
    audit(conn, "legalclaw_document", doc_id, "legal-update-legal-document",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": doc_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 3. get-legal-document
# ---------------------------------------------------------------------------
def get_legal_document(conn, args):
    doc_id = getattr(args, "document_id", None)
    doc = _validate_document(conn, doc_id)
    ok(row_to_dict(doc))


# ---------------------------------------------------------------------------
# 4. list-legal-documents
# ---------------------------------------------------------------------------
def list_legal_documents(conn, args):
    conditions = []
    params = []
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        conditions.append(_doc.matter_id == P())
        params.append(matter_id)
    document_type = getattr(args, "document_type", None)
    if document_type:
        conditions.append(_doc.document_type == P())
        params.append(document_type)
    document_status = getattr(args, "document_status", None)
    if document_status:
        conditions.append(_doc.status == P())
        params.append(document_status)
    if args.company_id:
        conditions.append(_doc.company_id == P())
        params.append(args.company_id)

    q = Q.from_(_doc).select(_doc.star)
    for cond in conditions:
        q = q.where(cond)
    q = q.orderby(_doc.updated_at, order=Order.desc).limit(P()).offset(P())

    rows = conn.execute(q.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"documents": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 5. file-document
# ---------------------------------------------------------------------------
def file_document(conn, args):
    doc_id = getattr(args, "document_id", None)
    doc = _validate_document(conn, doc_id)

    if doc["status"] == "filed":
        err(f"Document {doc_id} is already filed")
    if doc["status"] == "archived":
        err(f"Document {doc_id} is archived")

    filed_date = getattr(args, "filed_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    court_reference = getattr(args, "court_reference", None)

    now = _now_iso()
    data = {"status": "filed", "filed_date": filed_date, "updated_at": now}
    if court_reference:
        data["court_reference"] = court_reference
    sql, params = dynamic_update("legalclaw_document", data, where={"id": doc_id})
    conn.execute(sql, params)
    audit(conn, "legalclaw_document", doc_id, "legal-file-document",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": doc_id, "document_status": "filed", "filed_date": filed_date})


# ---------------------------------------------------------------------------
# 6. archive-document
# ---------------------------------------------------------------------------
def archive_document(conn, args):
    doc_id = getattr(args, "document_id", None)
    doc = _validate_document(conn, doc_id)

    if doc["status"] == "archived":
        err(f"Document {doc_id} is already archived")

    now = _now_iso()
    sql, params = dynamic_update("legalclaw_document",
        {"status": "archived", "updated_at": now},
        where={"id": doc_id})
    conn.execute(sql, params)
    audit(conn, "legalclaw_document", doc_id, "legal-archive-document",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": doc_id, "document_status": "archived"})


# ---------------------------------------------------------------------------
# 7. search-legal-documents
# ---------------------------------------------------------------------------
def search_legal_documents(conn, args):
    search = getattr(args, "search", None)
    if not search:
        err("--search is required")

    conditions = [
        _doc.title.like(P()) | _doc.content.like(P()) | _doc.file_name.like(P())
    ]
    params = [f"%{search}%", f"%{search}%", f"%{search}%"]

    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        conditions.append(_doc.matter_id == P())
        params.append(matter_id)
    document_type = getattr(args, "document_type", None)
    if document_type:
        conditions.append(_doc.document_type == P())
        params.append(document_type)
    if args.company_id:
        conditions.append(_doc.company_id == P())
        params.append(args.company_id)

    q = Q.from_(_doc).select(_doc.star)
    for cond in conditions:
        q = q.where(cond)
    q = q.orderby(_doc.updated_at, order=Order.desc).limit(P()).offset(P())

    rows = conn.execute(q.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"documents": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 8. add-document-version
# ---------------------------------------------------------------------------
def add_document_version(conn, args):
    doc_id = getattr(args, "document_id", None)
    doc = _validate_document(conn, doc_id)

    if doc["status"] == "archived":
        err(f"Document {doc_id} is archived and cannot be versioned")

    current_version = doc["version"]
    try:
        new_version = str(int(current_version) + 1)
    except ValueError:
        new_version = str(float(current_version) + 1)

    content = getattr(args, "content", None)
    now = _now_iso()

    data = {"version": new_version, "content": content or doc["content"], "updated_at": now}
    sql, params = dynamic_update("legalclaw_document", data, where={"id": doc_id})
    conn.execute(sql, params)

    # Reset status to draft when new version is created
    if doc["status"] in ("final", "filed"):
        sql2, params2 = dynamic_update("legalclaw_document", {"status": "draft"}, where={"id": doc_id})
        conn.execute(sql2, params2)

    audit(conn, "legalclaw_document", doc_id, "legal-add-document-version",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": doc_id, "previous_version": current_version, "new_version": new_version,
        "document_status": "draft"})


# ---------------------------------------------------------------------------
# 9. list-document-versions
# ---------------------------------------------------------------------------
def list_document_versions(conn, args):
    """Returns the current document with its version history from audit_log."""
    doc_id = getattr(args, "document_id", None)
    doc = _validate_document(conn, doc_id)
    d = row_to_dict(doc)

    # Get version history from audit log
    hist_q = (
        Q.from_(_audit)
        .select(_audit.timestamp, _audit.action, _audit.description)
        .where(_audit.entity_type == "legalclaw_document")
        .where(_audit.entity_id == P())
        .orderby(_audit.timestamp, order=Order.desc)
    )
    history = conn.execute(hist_q.get_sql(), (doc_id,)).fetchall()

    ok({
        "document_id": doc_id,
        "title": d["title"],
        "current_version": d["version"],
        "document_status": d["status"],
        "history": [row_to_dict(h) for h in history],
        "history_count": len(history),
    })


# ---------------------------------------------------------------------------
# 10. document-index
# ---------------------------------------------------------------------------
def document_index(conn, args):
    matter_id = getattr(args, "matter_id", None)
    if not matter_id:
        err("--matter-id is required")
    mq = Q.from_(_matter).select(_matter.id).where(_matter.id == P())
    if not conn.execute(mq.get_sql(), (matter_id,)).fetchone():
        err(f"Matter {matter_id} not found")

    q = (
        Q.from_(_doc)
        .select(
            _doc.id, _doc.naming_series, _doc.title, _doc.document_type, _doc.version,
            _doc.status, _doc.filed_date, _doc.court_reference, _doc.created_at, _doc.updated_at,
        )
        .where(_doc.matter_id == P())
        .orderby(_doc.document_type).orderby(_doc.title)
    )
    rows = conn.execute(q.get_sql(), (matter_id,)).fetchall()

    # Group by type
    by_type = {}
    for r in rows:
        dt = r["document_type"]
        if dt not in by_type:
            by_type[dt] = []
        by_type[dt].append(row_to_dict(r))

    ok({
        "matter_id": matter_id,
        "total_documents": len(rows),
        "by_type": by_type,
        "documents": [row_to_dict(r) for r in rows],
    })


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------
ACTIONS = {
    "legal-add-legal-document": add_legal_document,
    "legal-update-legal-document": update_legal_document,
    "legal-get-legal-document": get_legal_document,
    "legal-list-legal-documents": list_legal_documents,
    "legal-file-document": file_document,
    "legal-archive-document": archive_document,
    "legal-search-legal-documents": search_legal_documents,
    "legal-add-document-version": add_document_version,
    "legal-list-document-versions": list_document_versions,
    "legal-document-index": document_index,
}
