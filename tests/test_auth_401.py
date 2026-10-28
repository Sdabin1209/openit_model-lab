"""CONVENTIONS.md 6절 '401과 403'(D106): 영역 사이 서비스 API도 토큰이 없거나 틀리면 401 + `WWW-Authenticate: Bearer`,
토큰은 맞지만 권한이 없을 때(읽기 토큰의 쓰기)만 403. 오류 본문 code는 어느 쪽이나 PERMISSION_DENIED.
기대값은 계약 문서와 RFC 9110 15.5.2·11.6.1, RFC 6750 3절에서 왔다. raw 소켓으로 응답 줄과 헤더를 직접 본다."""
import json
import socket

import pytest

from tests.test_service import server  # noqa: F401  (fixture)


def http(server, method, path, token=None, body=b"", extra=b""):
    host, port = server.replace("http://", "").split(":")
    s = socket.create_connection((host, int(port))); s.settimeout(5)
    auth = b"Authorization: Bearer " + token.encode() + b"\r\n" if token is not None else b""
    s.sendall(f"{method} {path} HTTP/1.1\r\nHost: x\r\n".encode() + auth + extra +
              b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
    data = b""
    while chunk := s.recv(65536):
        data += chunk
    s.close()
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    hdrs = {l.split(b":", 1)[0].decode().lower(): l.split(b":", 1)[1].strip().decode() for l in lines[1:] if b":" in l}
    return int(lines[0].split()[1]), hdrs, rest


@pytest.mark.parametrize("method,token", [("GET", None), ("GET", "wrong"), ("GET", ""), ("POST", None), ("POST", "wrong")])
def test_missing_or_wrong_token_is_401_with_www_authenticate(server, method, token):
    status, h, body = http(server, method, "/v1/packages", token, b"x" if method == "POST" else b"")
    assert status == 401
    assert h.get("www-authenticate") == "Bearer"
    assert json.loads(body)["code"] == "PERMISSION_DENIED"


def test_valid_read_token_cannot_publish_is_403_without_challenge(server):
    status, h, body = http(server, "POST", "/v1/packages", "rt", b"x")
    assert status == 403
    assert "www-authenticate" not in h                      # 토큰은 맞다: 다시 인증해도 소용없다(RFC 9110 15.5.4)
    assert json.loads(body)["code"] == "PERMISSION_DENIED"


def test_valid_tokens_pass_and_health_needs_no_token(server):
    assert http(server, "GET", "/v1/packages", "rt")[0] == 200
    assert http(server, "GET", "/v1/packages", "wt")[0] == 200
    assert http(server, "GET", "/healthz")[0] == 200 and http(server, "GET", "/readyz")[0] == 200


def test_401_on_post_with_empty_body(server):
    status, h, _ = http(server, "POST", "/v1/packages", "wrong", b"", extra=b"X-Pad: 1\r\n")
    assert status == 401 and h.get("www-authenticate") == "Bearer"
