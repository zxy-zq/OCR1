import base64
import os
import threading
import unittest
import warnings
from unittest.mock import patch

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)
from fastapi.testclient import TestClient

from ocr_server import build_rapidocr_params, create_app


class OCRServerTest(unittest.TestCase):
    def test_post_ocr_returns_measure_fields_from_base64_json(self):
        app = create_app(
            ocr_runner=lambda image: [
                "位置 107.7237/31.35492",
                "NR-Cl 38698274839",
                "(9447821-23)",
                "SS-RSRP -110",
            ]
        )
        client = TestClient(app)

        response = client.post(
            "/api/measure/ocr",
            json={"image_base64": base64.b64encode(b"fake-image").decode("ascii")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "network": "NR",
                "location": "107.7237/31.35492",
                "cell_id": "38698274839(9447821-23)",
                "signal": -110,
            },
        )

    def test_build_rapidocr_params_reads_performance_environment(self):
        with patch.dict(
            os.environ,
            {
                "OCR_LOG_LEVEL": "error",
                "OCR_USE_CLS": "false",
                "OCR_ORT_INTRA_THREADS": "2",
                "OCR_ORT_INTER_THREADS": "1",
            },
            clear=False,
        ):
            params = build_rapidocr_params()

        self.assertEqual(params["Global.log_level"], "error")
        self.assertEqual(params["Global.use_cls"], False)
        self.assertEqual(params["EngineConfig.onnxruntime.intra_op_num_threads"], 2)
        self.assertEqual(params["EngineConfig.onnxruntime.inter_op_num_threads"], 1)

    def test_app_lifespan_preloads_ocr_runner_when_enabled(self):
        calls = []
        app = create_app(
            ocr_runner=lambda image: [],
            preload_runner=lambda: calls.append("preloaded"),
            preload=True,
        )

        with TestClient(app) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, ["preloaded"])

    def test_successful_ocr_request_logs_info(self):
        app = create_app(
            ocr_runner=lambda image: [
                "NR-Cl 38698274839",
                "(9447821-23)",
                "SS-RSRP -110",
            ],
            preload=False,
        )

        with self.assertLogs("ocr_server", level="INFO") as logs:
            with TestClient(app) as client:
                response = client.post(
                    "/api/measure/ocr",
                    json={
                        "image_base64": base64.b64encode(b"fake-image").decode(
                            "ascii"
                        )
                    },
                )

        self.assertEqual(response.status_code, 200)
        log_text = "\n".join(logs.output)
        self.assertIn("OCR request success", log_text)
        self.assertIn("input=base64", log_text)
        self.assertIn("lines=3", log_text)
        self.assertIn("network=NR", log_text)
        self.assertIn("signal=-110", log_text)

    def test_concurrency_limit_returns_429_when_capacity_is_busy(self):
        entered = threading.Event()
        release = threading.Event()
        responses = []

        def slow_runner(image):
            entered.set()
            release.wait(timeout=2)
            return []

        app = create_app(
            ocr_runner=slow_runner,
            max_concurrency=1,
            acquire_timeout_s=0.01,
        )

        def send_first_request():
            with TestClient(app) as client:
                responses.append(
                    client.post(
                        "/api/measure/ocr",
                        json={
                            "image_base64": base64.b64encode(b"first").decode(
                                "ascii"
                            )
                        },
                    )
                )

        thread = threading.Thread(target=send_first_request)
        thread.start()
        self.assertTrue(entered.wait(timeout=1))

        with self.assertLogs("ocr_server", level="WARNING") as logs:
            with TestClient(app) as client:
                second = client.post(
                    "/api/measure/ocr",
                    json={"image_base64": base64.b64encode(b"second").decode("ascii")},
                )

        release.set()
        thread.join(timeout=2)

        self.assertEqual(second.status_code, 429)
        self.assertEqual(responses[0].status_code, 200)
        self.assertIn("OCR capacity busy", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
