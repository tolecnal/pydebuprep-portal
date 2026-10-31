"""A stand-in OpenID Connect provider: a real HTTP server issuing RS256 tokens."""

from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from joserfc import jwt
from joserfc.jwk import RSAKey


class OIDCStub:
    """Discovery, JWKS and a token endpoint; codes are made by `code_for`."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig"})
        self.other_key = RSAKey.generate_key(2048, parameters={"kid": "k1"})  # forged, same kid
        self.new_key = RSAKey.generate_key(2048, parameters={"kid": "k2", "use": "sig"})
        self.stranger_key = RSAKey.generate_key(2048, parameters={"kid": "k9"})
        self.end_session = True
        self.jwks_fetches = 0
        self.codes: dict[str, dict[str, Any]] = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.issuer = f"http://127.0.0.1:{self.server.server_address[1]}/realms/test"
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # --- tokens -----------------------------------------------------------------

    def claims(self, **override: Any) -> dict[str, Any]:
        now = int(time.time())
        base = {
            "iss": self.issuer,
            "aud": "portal",
            "sub": "user-1",
            "name": "Ada Admin",
            "groups": ["pydebuprep-admins"],
            "iat": now,
            "exp": now + 300,
        }
        return {k: v for k, v in (base | override).items() if v is not None}

    def sign(self, claims: dict[str, Any], key: RSAKey | None = None) -> str:
        key = key or self.key
        return jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)

    def access_token(self, **override: Any) -> str:
        return self.sign(self.claims(**override))

    def code_for(self, nonce: str, /, **override: Any) -> str:
        code = secrets.token_urlsafe(16)
        self.codes[code] = self.claims(**({"nonce": nonce} | override))
        return code

    # --- HTTP ---------------------------------------------------------------------

    def _metadata(self) -> dict[str, Any]:
        base = self.issuer
        data = {
            "issuer": base,
            "authorization_endpoint": f"{base}/auth",
            "token_endpoint": f"{base}/token",
            "jwks_uri": f"{base}/certs",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
        if self.end_session:
            data["end_session_endpoint"] = f"{base}/logout"
        return data

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _json(self, status: int, data: object) -> None:
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path.endswith("/.well-known/openid-configuration"):
                    self._json(200, stub._metadata())
                elif path.endswith("/certs"):
                    stub.jwks_fetches += 1
                    self._json(200, {"keys": [stub.key.as_dict(private=False)]})
                else:
                    self._json(404, {})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                form = parse_qs(self.rfile.read(length).decode())
                claims = stub.codes.pop(form.get("code", [""])[0], None)
                if claims is None:
                    self._json(400, {"error": "invalid_grant"})
                    return
                token = stub.sign(claims)
                self._json(200, {"access_token": token, "id_token": token,
                                 "token_type": "Bearer", "expires_in": 300})  # fmt: skip

        return Handler
