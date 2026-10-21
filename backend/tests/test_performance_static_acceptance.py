"""Static optimization preserves content/cache contracts and bypasses real-time APIs."""
import gzip

from fastapi import FastAPI, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from app import main


def test_hashed_assets_gzip_identity_and_conditional_cache(tmp_path):
    source = ("const synthetic='Repeated safe fixture text';\n" * 5000).encode()
    (tmp_path / "index-A1b2C3d4.js").write_bytes(source)
    (tmp_path / "plain.js").write_bytes(b"short")
    app = FastAPI()
    app.mount("/assets", GZipMiddleware(main.FrontendAssets(directory=tmp_path), minimum_size=1024, compresslevel=6))
    client = TestClient(app)
    with client.stream("GET", "/assets/index-A1b2C3d4.js", headers={"Accept-Encoding": "gzip"}) as response:
        wire = b"".join(response.iter_raw())
        assert response.status_code == 200 and response.headers["content-encoding"] == "gzip"
        assert gzip.decompress(wire) == source
        assert len(wire) < len(source) // 10
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert "Accept-Encoding" in response.headers["vary"]
        etag = response.headers["etag"]
    identity = client.get("/assets/index-A1b2C3d4.js", headers={"Accept-Encoding": "identity"})
    assert identity.content == source and "content-encoding" not in identity.headers
    cached = client.get("/assets/index-A1b2C3d4.js", headers={"If-None-Match": etag, "Accept-Encoding": "gzip"})
    assert cached.status_code == 304 and cached.content == b""
    assert cached.headers["cache-control"].endswith("immutable")
    plain = client.get("/assets/plain.js")
    assert plain.content == b"short" and "cache-control" not in plain.headers


def test_index_revalidates_and_static_mount_does_not_compress_api_streams_or_websockets(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>Synthetic index</html>")
    monkeypatch.setattr(main, "frontend_dist", tmp_path)
    assert main.frontend("").headers["cache-control"] == "no-cache"
    assert main.frontend("arbitrary/client-route").headers["cache-control"] == "no-cache"
    app = FastAPI()
    app.mount("/assets", GZipMiddleware(main.FrontendAssets(directory=tmp_path), minimum_size=1024, compresslevel=6))

    @app.get("/api/events")
    def events():
        return StreamingResponse(iter(["data: synthetic\n\n" * 100]), media_type="text/event-stream")

    @app.websocket("/api/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_json({"type": "synthetic-status"})
        await socket.close()

    client = TestClient(app)
    response = client.get("/api/events", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in response.headers and response.text.startswith("data: synthetic")
    with client.websocket_connect("/api/ws") as socket:
        assert socket.receive_json() == {"type": "synthetic-status"}
