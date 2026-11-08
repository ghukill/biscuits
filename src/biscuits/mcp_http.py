"""MCP over HTTP at <base_path>/mcp, so programs reach OpenProject through biscuits.

The Streamable HTTP transport, in its simplest form: each POST carries one
JSON-RPC message (or a batch) and gets a JSON reply. There's no session and no
server-to-client stream, so a GET is refused.

Who's calling: a biscuits API token (tokens.py) as `Authorization: Bearer ...`.
The call then acts as that token's account, with that account's OpenProject key,
and its writes pass the [writes] gate and go in the write log.

Where from: only addresses in [mcp] allow_from (by default this machine and the
tailnet). That's checked before the token, so the endpoint isn't open to the
public internet even behind a public proxy.

This blueprint is separate from the UI's, so the login session and its
X-Biscuits header check don't apply here: a bearer token isn't sent by browsers
on their own, which is what that check guards against.
"""

import ipaddress
import json

from flask import Blueprint, Response, jsonify, request

from . import __version__, accounts, client, config, mcp_tools, tokens

mcp_bp = Blueprint("mcp", __name__)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
# A 10 MB attachment as base64, plus the JSON around it.
MAX_REQUEST = 15 * 1024 * 1024
INSTRUCTIONS = (
    "OpenProject tasks, through biscuits. You act as the biscuits account this token belongs to, "
    "with its OpenProject permissions. Writes are limited by biscuits' [writes] settings and logged. "
    "Nothing can be deleted.")


def _rpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _http_error(status, message, **headers):
    resp = jsonify(_rpc_error(None, -32001 if status in (401, 403) else -32600, message))
    resp.status_code = status
    resp.headers.update(headers)
    return resp


def _allowed(address):
    try:
        ip = ipaddress.ip_address((address or "").split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in config.load().mcp_allow_from)


def _caller():
    """(connection, None) for a good token, or (None, error response)."""
    address = request.remote_addr or "?"
    if not _allowed(address):
        return None, _http_error(403, "biscuits' MCP endpoint doesn't accept connections from this address.")
    if accounts.limiter.blocked(address, "mcp-token"):
        return None, _http_error(429, "Too many bad tokens. Please wait a few minutes.")
    auth = request.headers.get("Authorization") or ""
    found = tokens.authenticate(auth[7:].strip()) if auth[:7].lower() == "bearer " else None
    if found is None:
        accounts.limiter.fail(address, "mcp-token")
        return None, _http_error(401, "A valid biscuits API token is required.",
                                 **{"WWW-Authenticate": 'Bearer realm="biscuits"'})
    user, token = found
    key = accounts.get_key(user)
    if not key:
        return None, _http_error(403, f"The biscuits account {user.username} isn't connected to OpenProject yet.")
    return client.OP(key, actor={"user_id": user.id, "username": user.username, "op_user_id": user.op_user_id,
                                 "via": "mcp", "token": token.name}), None


def _handle(op, msg):
    """The reply to one JSON-RPC message, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    method, params, req_id = msg["method"], msg.get("params") or {}, msg.get("id")
    if "id" not in msg:
        return None  # notifications/initialized and the like need no answer
    if method == "initialize":
        asked = params.get("protocolVersion") if isinstance(params, dict) else None
        result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "biscuits", "version": __version__},
                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": mcp_tools.TOOLS}
    elif method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("arguments") or {}, dict):
            return _rpc_error(req_id, -32602, "Invalid params")
        is_err, out = mcp_tools.call(op, params.get("name"), params.get("arguments") or {})
        result = {"isError": is_err, "content": [{"type": "text", "text": json.dumps(out, indent=1, default=str)}]}
    else:
        return _rpc_error(req_id, -32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


@mcp_bp.post("/mcp")
def endpoint():
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return _http_error(403, "Cross-origin requests aren't accepted.")
    op, error = _caller()
    if error:
        return error
    request.max_content_length = MAX_REQUEST
    try:
        body = json.loads(request.get_data(cache=False) or b"null")
    except ValueError:
        return _http_error(400, "Parse error: the body isn't JSON.")
    batch = isinstance(body, list)
    replies = [r for r in (_handle(op, m) for m in (body if batch else [body])) if r is not None]
    if not replies:
        return Response(status=202)
    return jsonify(replies if batch else replies[0])


@mcp_bp.route("/mcp", methods=["GET", "DELETE"])
def no_stream():
    return Response(status=405, headers={"Allow": "POST"})
