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

    ENTITY_PREFIXES.setdefault("legalclaw_document", "LDOC-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_DOCUMENT_TYPES = (
    "pleading", "motion", "brief", "contract", "correspondence",
    "discovery", "evidence", "order", "general", "other",
)
VALID_DOCUMENT_STATUSES = ("draft", "review", "final", "filed", "archived")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    if not conn.execute("SELECT id FROM company WHERE id = ?", (company_id,)).fetchone():
        err(f"Company {company_id} not found")


def _validate_document(conn, doc_id):
    if not doc_id:
        err("--document-id is required")
    row = conn.execute("SELECT * FROM legalclaw_document WHERE id = ?", (doc_id,)).fetchone()
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
        if not conn.execute("SELECT id FROM legalclaw_matter WHERE id = ?", (matter_id,)).fetchone():
            err(f"Matter {matter_id} not found")

    doc_id = str(uuid.uuid4())
    ns = get_next_name(conn, "legalclaw_document", company_id=args.company_id)
    now = _now_iso()

    conn.execute("""
        INSERT INTO legalclaw_document (
            id, naming_series, matter_id, title, document_type, file_name,
            content, version, status, filed_date, court_reference,
            company_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
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

    updates, params, changed = [], [], []
    for arg_name, col_name in {
        "doc_title": "title", "document_type": "document_type",
        "file_name": "file_name", "content": "content",
        "court_reference": "court_reference",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            if arg_name == "document_type":
                _validate_enum(val, VALID_DOCUMENT_TYPES, "document-type")
            updates.append(f"{col_name} = ?")
            params.append(val)
            changed.append(col_name)

    document_status = getattr(args, "document_status", None)
    if document_status:
        _validate_enum(document_status, VALID_DOCUMENT_STATUSES, "document-status")
        updates.append("status = ?")
        params.append(document_status)
        changed.append("status")

    if not updates:
        err("No fields to update")

    updates.append("updated_at = ?")
    params.append(_now_iso())
    params.append(doc_id)
    conn.execute(f"UPDATE legalclaw_document SET {', '.join(updates)} WHERE id = ?", params)
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
    sql = "SELECT * FROM legalclaw_document WHERE 1=1"
    params = []
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        sql += " AND matter_id = ?"
        params.append(matter_id)
    document_type = getattr(args, "document_type", None)
    if document_type:
        sql += " AND document_type = ?"
        params.append(document_type)
    document_status = getattr(args, "document_status", None)
    if document_status:
        sql += " AND status = ?"
        params.append(document_status)
    if args.company_id:
        sql += " AND company_id = ?"
        params.append(args.company_id)
    sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
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
    updates = "status = 'filed', filed_date = ?, updated_at = ?"
    params = [filed_date, now]
    if court_reference:
        updates += ", court_reference = ?"
        params.append(court_reference)
    params.append(doc_id)

    conn.execute(f"UPDATE legalclaw_document SET {updates} WHERE id = ?", params)
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
    conn.execute("UPDATE legalclaw_document SET status = 'archived', updated_at = ? WHERE id = ?",
                 (now, doc_id))
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

    sql = """SELECT * FROM legalclaw_document
             WHERE (title LIKE ? OR content LIKE ? OR file_name LIKE ?)"""
    params = [f"%{search}%", f"%{search}%", f"%{search}%"]

    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        sql += " AND matter_id = ?"
        params.append(matter_id)
    document_type = getattr(args, "document_type", None)
    if document_type:
        sql += " AND document_type = ?"
        params.append(document_type)
    if args.company_id:
        sql += " AND company_id = ?"
        params.append(args.company_id)

    sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
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

    conn.execute("""
        UPDATE legalclaw_document SET version = ?, content = ?, updated_at = ?
        WHERE id = ?
    """, (new_version, content or doc["content"], now, doc_id))

    # Reset status to draft when new version is created
    if doc["status"] in ("final", "filed"):
        conn.execute("UPDATE legalclaw_document SET status = 'draft' WHERE id = ?", (doc_id,))

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
    history = conn.execute("""
        SELECT timestamp, action, description
        FROM audit_log
        WHERE entity_type = 'legalclaw_document' AND entity_id = ?
        ORDER BY timestamp DESC
    """, (doc_id,)).fetchall()

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
    if not conn.execute("SELECT id FROM legalclaw_matter WHERE id = ?", (matter_id,)).fetchone():
        err(f"Matter {matter_id} not found")

    rows = conn.execute("""
        SELECT id, naming_series, title, document_type, version, status,
               filed_date, court_reference, created_at, updated_at
        FROM legalclaw_document
        WHERE matter_id = ?
        ORDER BY document_type, title
    """, (matter_id,)).fetchall()

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
