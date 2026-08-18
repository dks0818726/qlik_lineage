import base64
import os
import socket
import ssl

import requests

HOST = "10.221.11.6"
PORT = 443
XRF = "1234567890abcdef"
VERIFY = False
BASE = f"https://{HOST}/custom"
APP_ID = "2cdeb4ab-118e-4107-b9ee-62052d71e5a7"

requests.packages.urllib3.disable_warnings()


def rest_probe(user: str) -> tuple[int, str | None]:
    sess = requests.Session()
    headers = {
        "X-Qlik-Xrfkey": XRF,
        "Content-Type": "application/json",
    }
    if user:
        headers["X-Qlik-User"] = user

    response = sess.get(
        f"{BASE}/qrs/about?xrfkey={XRF}",
        headers=headers,
        verify=VERIFY,
        timeout=30,
    )

    cookie = None
    for name, value in sess.cookies.items():
        if name.startswith("X-Qlik-Session"):
            cookie = f"{name}={value}"
            break
    return response.status_code, cookie


def ws_probe(path: str, user: str, cookie: str | None) -> str:
    key = base64.b64encode(os.urandom(16)).decode()
    cookie_header = f"Cookie: {cookie}\r\n" if cookie else ""
    user_header = f"X-Qlik-User: {user}\r\n" if user else ""

    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {HOST}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Origin: https://{HOST}\r\n"
        f"X-Qlik-Xrfkey: {XRF}\r\n"
        f"{user_header}{cookie_header}"
        "\r\n"
    )

    ctx = ssl.create_default_context()
    if not VERIFY:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    with socket.create_connection((HOST, PORT), timeout=15) as raw:
        with ctx.wrap_socket(raw, server_hostname=HOST) as tls:
            tls.sendall(request.encode())
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 8192:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                data += chunk

    return data.decode("latin-1").split("\r\n", 1)[0]


def main() -> None:
    users = [
        ("domain_backslash", "CORPORATE\\srv-qlik"),
        ("ud_uid", "UserDirectory=CORPORATE;UserId=srv-qlik"),
        ("empty", ""),
    ]

    print("=== REST probe by user format ===")
    rest_results = []
    for label, user in users:
        try:
            status, cookie = rest_probe(user)
            cookie_name = cookie.split("=", 1)[0] if cookie else "NONE"
            print(f"{label:18} status={status} cookie={cookie_name}")
            rest_results.append((label, user, status, cookie))
        except Exception as exc:
            print(f"{label:18} ERROR {exc}")

    ok = [row for row in rest_results if row[2] == 200]
    if not ok:
        print("No REST-authenticated variant found; skipping WS matrix.")
        return

    label, user, _, cookie = ok[0]
    print(f"\nUsing first REST-OK identity: {label} -> {user}")

    paths = [
        f"/custom/app/engineData?xrfkey={XRF}",
        f"/custom/app/{APP_ID}?xrfkey={XRF}",
        f"/custom/sense/app/{APP_ID}?xrfkey={XRF}",
        f"/caddie/app/{APP_ID}?xrfkey={XRF}",
    ]

    print("\n=== WS path probe (same authenticated session) ===")
    for path in paths:
        try:
            status = ws_probe(path, user, cookie)
            print(f"{path:72} -> {status}")
        except Exception as exc:
            print(f"{path:72} -> ERROR {exc}")


if __name__ == "__main__":
    main()
