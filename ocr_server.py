from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Callable, Iterable, Optional, Union

import anyio
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from rapidocr import RapidOCR

from measure_parser import parse_measure_ocr


ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / ".models" / "rapidocr"
ImageInput = Union[str, Path, bytes]
OCRRunner = Callable[[ImageInput], Iterable[str]]

_engine: Optional[RapidOCR] = None


def get_engine() -> RapidOCR:
    global _engine
    if _engine is None:
        _engine = RapidOCR(
            params={
                "Global.model_root_dir": str(MODEL_DIR),
                "Global.log_level": "warning",
            }
        )
    return _engine


def run_rapidocr(image: ImageInput) -> list[str]:
    result = get_engine()(image)
    return list(result.txts or [])


def create_app(ocr_runner: OCRRunner = run_rapidocr) -> FastAPI:
    app = FastAPI(title="Measure OCR Server")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/measure/ocr")
    async def measure_ocr(
        request: Request,
        file: UploadFile | None = File(default=None),
        image: UploadFile | None = File(default=None),
    ) -> dict:
        image_input = await read_image_input(request, file or image)
        lines = await anyio.to_thread.run_sync(ocr_runner, image_input)
        return parse_measure_ocr(lines)

    return app


async def read_image_input(
    request: Request, upload: UploadFile | None = None
) -> ImageInput:
    if upload is not None:
        content = await upload.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded image is empty.")
        return content

    payload = await read_json_payload(request)

    image_base64 = payload.get("image_base64")
    if image_base64:
        return decode_base64_image(str(image_base64))

    image_path = payload.get("image_path")
    if image_path:
        return resolve_image_path(str(image_path))

    raise HTTPException(
        status_code=400,
        detail="Send multipart file, JSON image_base64, or JSON image_path.",
    )


async def read_json_payload(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Request body must be multipart/form-data or JSON.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object.")
    return payload


def decode_base64_image(value: str) -> bytes:
    encoded = value.split(",", 1)[1] if "," in value else value
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image_base64 is invalid.") from exc


def resolve_image_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=400, detail=f"Image not found: {path}")
    return path


app = create_app()
