import base64
import unittest
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)
from fastapi.testclient import TestClient

from ocr_server import create_app


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


if __name__ == "__main__":
    unittest.main()
