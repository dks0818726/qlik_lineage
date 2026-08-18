import base64
import os
import socket
import ssl

import requests

QLIK_HOST = "10.221.11.6"
QLIK_PORT = 443
XRFKEY = "1234567890abcdef"
QLIK_USER = "CORPORATE\\srv-qlik"
VERIFY_SSL = False

BASE = f"https://{QLIK_HOST}/custom"
REST_HEADERS = {
    "X-Qlik-Xrfkey": XRFKEY,
    "X-Qlik-User": QLIK_USER,
    "Content-Type": "application/json",
}

requests.packages.urllib3.disable_warnings()


def get_session_cookie() -> tuple[str | None, str | None]:
    """Use a requests.Session so cookies set by the proxy persist,
    then pull out the X-Qlik-Session value explicitly."""
    sess = requests.Session()
    r = sess.get(
        f"{BASE}/qrs/about?xrfkey={XRFKEY}",
        headers=REST_HEADERS, verify=VERIFY_SSL, timeout=30,
    )
    print(f"GET /custom/qrs/about -> HTTP {r.status_code}")
    print(f"Set-Cookie seen       -> {r.headers.get('Set-Cookie')}")

    cookie_val = None
    for name, value in sess.cookies.items():
        if name.startswith("X-Qlik-Session"):
            cookie_val = f"{name}={value}"
            break
    if cookie_val:
        print(f"Resolved session cookie -> {cookie_val.split('=', 1)[0]}")

    app_id = None
    r2 = sess.get(
        f"{BASE}/qrs/app/full?xrfkey={XRFKEY}",
        headers=REST_HEADERS, verify=VERIFY_SSL, timeout=60,
    )
    if r2.status_code == 200 and r2.json():
        app_id = r2.json()[0].get("id")
    return cookie_val, app_id


def ws_handshake(path: str, session_cookie: str | None) -> None:
    key = base64.b64encode(os.urandom(16)).decode()
    cookie_header = f"Cookie: {session_cookie}\r\n" if session_cookie else ""

    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {QLIK_HOST}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Origin: https://{QLIK_HOST}\r\n"
        f"X-Qlik-Xrfkey: {XRFKEY}\r\n"
        f"X-Qlik-User: {QLIK_USER}\r\n"
        f"{cookie_header}"
        "\r\n"
    )

    ctx = ssl.create_default_context()
    if not VERIFY_SSL:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    print(f"\nwss://{QLIK_HOST}{path}  (cookie {'present' if session_cookie else 'MISSING'})")
    with socket.create_connection((QLIK_HOST, QLIK_PORT), timeout=15) as raw:
        with ctx.wrap_socket(raw, server_hostname=QLIK_HOST) as tls:
            tls.sendall(request.encode())
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 8192:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                data += chunk
    head = data.decode("latin-1").split("\r\n\r\n", 1)[0]
    print(f"  RESULT -> {head.splitlines()[0]}")


def main():
    cookie, app_id = get_session_cookie()
    aid = app_id or "engineData"
    ws_handshake(f"/custom/app/{aid}?xrfkey={XRFKEY}", cookie)


if __name__ == "__main__":
    main()