"""Conservative transport classification; a retry is not an ability failure."""
NETWORK_CODES = {
    "MODEL_NETWORK_ERROR", "MODEL_STREAM_DISCONNECTED", "MODEL_STREAM_INCOMPLETE",
    "MODEL_FIRST_TOKEN_TIMEOUT", "MODEL_IDLE_TIMEOUT", "MODEL_TOTAL_TIMEOUT",
}


def is_network_error(data):
    code = data.get("error_code") or (data.get("output") or {}).get("metadata", {}).get("error_code")
    if code:
        return code in NETWORK_CODES
    error = str(data.get("error") or "")
    return any(marker in error for marker in (
        *NETWORK_CODES, "ConnectError:", "ReadError:", "ConnectTimeout:", "ReadTimeout:",
        "RemoteProtocolError:",
    ))
