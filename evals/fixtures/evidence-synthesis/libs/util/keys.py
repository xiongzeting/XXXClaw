def normalize_ledger_key(raw):
    """Canonical ledger key: lowercase, dash-joined."""
    return "-".join(str(raw).strip().lower().split())
