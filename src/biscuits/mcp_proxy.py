"""`biscuits mcp`: a stdio MCP server that passes everything to a biscuits server.

For MCP clients that can only start a local process (pi, the Mattermost bots).
Each JSON-RPC line from stdin is POSTed to the biscuits MCP endpoint, and the
reply is written to stdout. It never talks to OpenProject itself.

The one thing it does locally: add_attachment with a `file_path` reads that file
here, on the caller's machine, and sends it as content_base64, since the biscuits
server can't (and mustn't) read files where it runs.

Settings, from the environment:

  BISCUITS_MCP_URL          the endpoint, e.g. http://your-biscuits-host:8000/mcp
  BISCUITS_MCP_TOKEN_FILE   a file holding the biscuits API token (preferred:
                            it keeps the token out of the environment)
  BISCUITS_MCP_TOKEN        the token itself, if there's no file

Standard library only, and it doesn't read the biscuits config.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request

MAX_ATTACHMENT = 10 * 1024 * 1024  # the server's limit too
TIMEOUT = 120


def settings():
    """(url, token). Either may be empty."""
    url = (os.environ.get("BISCUITS_MCP_URL") or "").strip()
    token = ""
    token_file = os.environ.get("BISCUITS_MCP_TOKEN_FILE")
    if token_file:
        try:
            with open(os.path.expanduser(token_file)) as f:
                token = f.read().strip()
        except OSError as e:
            sys.stderr.write(f"biscuits mcp: can't read BISCUITS_MCP_TOKEN_FILE: {e.strerror}\n")
    token = token or (os.environ.get("BISCUITS_MCP_TOKEN") or "").strip()
    return url, token


def _local_file(msg):
    """Turn add_attachment's file_path into content_base64. Returns an error reply, or None."""
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    args = params.get("arguments")
    if msg.get("method") != "tools/call" or params.get("name") != "add_attachment" or not isinstance(args, dict):
        return None
    if not args.get("file_path"):
        return None

    def refuse(text):
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {
            "isError": True, "content": [{"type": "text", "text": json.dumps({"_error": text})}]}}

    if args.get("content_base64"):
        return refuse("give exactly one of file_path or content_base64")
    path = os.path.expanduser(str(args["file_path"]))
    if not os.path.isfile(path):
        return refuse(f"no such file: {args['file_path']}")
    if os.path.getsize(path) > MAX_ATTACHMENT:
        return refuse(f"file is over {MAX_ATTACHMENT // (1024 * 1024)} MB")
    with open(path, "rb") as f:
        data = f.read()
    rest = {k: v for k, v in args.items() if k != "file_path"}
    params["arguments"] = {**rest, "content_base64": base64.b64encode(data).decode(),
                           "file_name": args.get("file_name") or os.path.basename(path)}
    msg["params"] = params
    return None


def forward(url, token, msg):
    """POST one message. Returns the reply (a dict), or None when there is none."""
    req = urllib.request.Request(
        url, method="POST", data=json.dumps(msg).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        try:
            reply = json.loads(e.read().decode())
            text = (reply.get("error") or {}).get("message") or f"HTTP {e.code}"
        except Exception:
            text = f"HTTP {e.code}"
        return _error(msg, f"biscuits refused the request: {text}")
    except (urllib.error.URLError, OSError) as e:
        return _error(msg, f"can't reach biscuits at {url}: {getattr(e, 'reason', e)}")
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return _error(msg, "biscuits sent back something that isn't JSON")


def _error(msg, text):
    if "id" not in msg:
        sys.stderr.write(f"biscuits mcp: {text}\n")
        return None
    return {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32000, "message": text}}


def main():
    url, token = settings()
    if not (url and token):
        sys.stderr.write("biscuits mcp: set BISCUITS_MCP_URL and BISCUITS_MCP_TOKEN_FILE (or BISCUITS_MCP_TOKEN)\n")
    else:
        sys.stderr.write(f"biscuits mcp: forwarding to {url}\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        if not (url and token):
            reply = _error(msg, "biscuits mcp isn't configured: set BISCUITS_MCP_URL and BISCUITS_MCP_TOKEN_FILE")
        else:
            reply = _local_file(msg) or forward(url, token, msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
