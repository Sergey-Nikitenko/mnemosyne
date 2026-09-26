"""Conformance: a provider transport failure stays bounded at the adapter boundary.

A transient transport failure (e.g. RemoteDisconnected when DeepSeek drops the
connection) must become a defined `ModelResponse(success=False, error=...)` at the
provider boundary — never an unclassified Python exception that propagates and kills the
worker. This pins that normalization and the no-secret invariant. It assumes no retry
policy (retries would need to earn their own requirement).

Run:  py tests/conformance/test_provider_transport_failure.py
"""
import http.client
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ModelRequest  # noqa: E402
from integrations.deepseek import DeepseekModel  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Conformance: provider transport failures are bounded at the adapter boundary")
    tmp = tempfile.mkdtemp()
    key_file = os.path.join(tmp, "key")
    secret = "sk-test-secret-key-12345"
    with open(key_file, "w", encoding="utf-8") as f:
        f.write(secret)

    model = DeepseekModel(api_key_file=key_file, model="deepseek-chat")
    req = ModelRequest(messages=[{"role": "user", "content": "hi"}])

    # a transient transport failure must NOT escape as an exception
    with mock.patch("urllib.request.urlopen",
                    side_effect=http.client.RemoteDisconnected("Remote end closed")):
        resp = model.run_model(req)
    check(resp.success is False, "transport failure -> success=False (bounded)")
    check("transport" in (resp.error or "").lower(), "the error names a transport failure")
    check(secret not in (resp.error or ""), "the secret never enters the error")

    # HTTPError (a defined provider response) still maps cleanly, secret-free
    with mock.patch("urllib.request.urlopen",
                    side_effect=http.client.HTTPException("boom")):
        resp2 = model.run_model(req)
    check(resp2.success is False and secret not in (resp2.error or ""),
          "generic HTTPException also bounded + secret-free")

    print("\nPASS: provider transport failures are normalized to a bounded model failure.")


if __name__ == "__main__":
    main()
