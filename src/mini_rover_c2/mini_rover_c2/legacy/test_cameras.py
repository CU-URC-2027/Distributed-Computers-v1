from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import MagicMock, patch
from mini_rover_c2.legacy.cameras import fetch_snapshot, SnapshotReader, MAX_BYTES


class CameraTests(unittest.TestCase):
    def test_reject_non_http_sources(self):
        for url in ("rtsp://camera/live", "file:///image.png", "0"):
            with self.assertRaises(ValueError):
                fetch_snapshot(url)

    def response(self, kind, data):
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "http://camera.test/image"
        response.headers = Message()
        response.headers["Content-Type"] = kind
        response.read.return_value = data
        return response

    def test_reject_html_mjpeg_and_oversized_images(self):
        for kind, data in (("text/html", b"error"), ("multipart/x-mixed-replace", b"stream"), ("image/png", b"x" * (MAX_BYTES + 1))):
            with patch("mini_rover_c2.legacy.cameras.urlopen", return_value=self.response(kind, data)):
                with self.assertRaises(ValueError):
                    fetch_snapshot("http://camera.test/image")

    def test_worker_reports_failure_without_exposing_url(self):
        def fail(url):
            raise OSError("secret-camera-address")
        reader = SnapshotReader(fetch=fail)
        reader.start("http://camera.test/image")
        try:
            data, _, _, error = reader.results.get(timeout=2)
            self.assertIsNone(data)
            self.assertIn("OSError", error)
            self.assertNotIn("secret", error)
        finally:
            reader.stop()

    def test_local_http_round_trip(self):
        payload = b"camera response"
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertEqual(fetch_snapshot(f"http://127.0.0.1:{server.server_port}/snapshot"), payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
