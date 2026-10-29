"""End-to-end checks for the local dashboard API."""

import io
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

from http.server import ThreadingHTTPServer
from server import DashboardHandler


class DashboardAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def get(self, path):
        return urlopen(self.url + path, timeout=15)

    def post(self, body):
        request = Request(
            self.url + "/api/simulate", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        return urlopen(request, timeout=15)

    def test_page_and_config_available(self):
        with self.get("/") as response:
            self.assertIn(b"<html", response.read().lower())
        with self.get("/api/config") as response:
            config = json.load(response)
        self.assertEqual(config["defaults"]["num_levels"], 10)
        self.assertIn("buyer_drift", config["limits"])

    def test_invalid_input_returns_useful_error(self):
        for settings in ([1, 2], {"unknown": 1}, {"buyer_volatility": -0.1}, {"num_events": True}):
            with self.subTest(settings=settings), self.assertRaises(HTTPError) as error:
                self.post(settings)
            self.assertEqual(error.exception.code, 400)
            self.assertTrue(json.load(error.exception)["error"])

    def test_generate_and_download_same_run(self):
        with self.post({"num_events": 200, "seed": 19}) as response:
            run = json.load(response)
        self.assertTrue(run["validation"]["passed"])
        self.assertEqual(len(run["events"]), 200)
        self.assertEqual(len(run["book_frames"]), 200)
        self.assertEqual(run["final_book"], run["book_frames"][-1])
        with self.get(f'/api/export?run_id={run["run_id"]}&format=zip') as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        names = archive.namelist()
        for name in ("SYNTH_message_10.csv", "SYNTH_orderbook_10.csv", "SYNTH_market_data.xlsx"):
            self.assertIn(name, names)
        messages = archive.read("SYNTH_message_10.csv").decode().splitlines()
        self.assertEqual(len(messages), 200)
        self.assertEqual(len(messages[0].split(",")), 6)

    def test_missing_run_and_nonpublic_file(self):
        for path in ("/api/export?run_id=missing", "/server.py", "/../README.md"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
