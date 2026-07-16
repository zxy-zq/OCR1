from __future__ import annotations

import base64
import binascii
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Union

import anyio
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from rapidocr import RapidOCR
from rapidocr.utils.typings import ModelType

from measure_parser import parse_measure_ocr


logger = logging.getLogger("ocr_server")
ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / ".models" / "rapidocr"
ImageInput = Union[str, Path, bytes]
OCRRunner = Callable[[ImageInput], Iterable[str]]
PreloadRunner = Callable[[], object]

_engine: Optional[RapidOCR] = None


class OCRLines(list):
    def __init__(self, lines: Iterable[str], profile: Optional[dict] = None):
        super().__init__(lines)
        self.profile = profile or {}


def build_rapidocr_params(env: Mapping[str, str] = os.environ) -> dict:
    params = {
        "Global.model_root_dir": str(MODEL_DIR),
        "Global.log_level": env.get("OCR_LOG_LEVEL", "warning"),
    }

    use_cls = parse_bool_env(env, "OCR_USE_CLS")
    if use_cls is not None:
        params["Global.use_cls"] = use_cls

    det_limit_side_len = parse_int_env(env, "OCR_DET_LIMIT_SIDE_LEN")
    if det_limit_side_len is not None:
        params["Det.limit_side_len"] = det_limit_side_len

    det_model_type = parse_model_type_env(env, "OCR_DET_MODEL_TYPE")
    if det_model_type is not None:
        params["Det.model_type"] = det_model_type

    for env_name, param_name in (
        (
            "OCR_ORT_INTRA_THREADS",
            "EngineConfig.onnxruntime.intra_op_num_threads",
        ),
        (
            "OCR_ORT_INTER_THREADS",
            "EngineConfig.onnxruntime.inter_op_num_threads",
        ),
    ):
        value = parse_int_env(env, env_name)
        if value is not None:
            params[param_name] = value

    use_cuda = parse_bool_env(env, "OCR_USE_CUDA")
    if use_cuda is not None:
        params["EngineConfig.onnxruntime.use_cuda"] = use_cuda

    use_dml = parse_bool_env(env, "OCR_USE_DML")
    if use_dml is not None:
        params["EngineConfig.onnxruntime.use_dml"] = use_dml

    return params


def parse_bool_env(env: Mapping[str, str], name: str) -> Optional[bool]:
    value = env.get(name)
    if value is None or value == "":
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_int_env(env: Mapping[str, str], name: str) -> Optional[int]:
    value = env.get(name)
    if value is None or value == "":
        return None
    return int(value)


def parse_model_type_env(env: Mapping[str, str], name: str) -> Optional[ModelType]:
    value = env.get(name)
    if value is None or value == "":
        return None

    normalized = value.strip().lower()
    model_types = {
        "tiny": ModelType.TINY,
        "small": ModelType.SMALL,
        "medium": ModelType.MEDIUM,
    }
    if normalized not in model_types:
        raise ValueError(f"{name} must be one of: tiny, small, medium")
    return model_types[normalized]


def parse_float_env(env: Mapping[str, str], name: str, default: float) -> float:
    value = env.get(name)
    if value is None or value == "":
        return default
    return float(value)


def get_engine() -> RapidOCR:
    global _engine
    if _engine is None:
        _engine = RapidOCR(params=build_rapidocr_params())
    return _engine


def run_rapidocr(image: ImageInput) -> list[str]:
    started = time.perf_counter()
    result = get_engine()(image)
    total_ms = (time.perf_counter() - started) * 1000
    return OCRLines(result.txts or [], profile=build_ocr_profile(result, total_ms))


def build_ocr_profile(result: object, total_ms: float) -> dict:
    elapse_list = list(getattr(result, "elapse_list", []) or [])
    det_ms = seconds_to_ms(elapse_list[0]) if len(elapse_list) > 0 else 0.0
    cls_ms = seconds_to_ms(elapse_list[1]) if len(elapse_list) > 1 else 0.0
    rec_ms = seconds_to_ms(elapse_list[2]) if len(elapse_list) > 2 else 0.0
    known_ms = det_ms + cls_ms + rec_ms
    return {
        "total_ms": total_ms,
        "det_ms": det_ms,
        "cls_ms": cls_ms,
        "rec_ms": rec_ms,
        "other_ms": max(0.0, total_ms - known_ms),
    }


def seconds_to_ms(value: object) -> float:
    try:
        return float(value) * 1000
    except (TypeError, ValueError):
        return 0.0


def format_ocr_profile(lines: Iterable[str]) -> str:
    profile = getattr(lines, "profile", None)
    if not profile:
        return "profile=unavailable"
    return (
        "profile=total_ms={total_ms:.1f} det_ms={det_ms:.1f} cls_ms={cls_ms:.1f} "
        "rec_ms={rec_ms:.1f} other_ms={other_ms:.1f}"
    ).format(**profile)


def create_app(
    ocr_runner: OCRRunner = run_rapidocr,
    *,
    max_concurrency: int | None = None,
    acquire_timeout_s: float | None = None,
    preload: bool | None = None,
    preload_runner: PreloadRunner | None = None,
) -> FastAPI:
    if max_concurrency is None:
        max_concurrency = parse_int_env(os.environ, "OCR_MAX_CONCURRENCY")
    if max_concurrency is None:
        max_concurrency = max(1, os.cpu_count() or 1)

    if acquire_timeout_s is None:
        acquire_timeout_s = parse_float_env(os.environ, "OCR_ACQUIRE_TIMEOUT_S", 1.0)

    if preload is None:
        configured_preload = parse_bool_env(os.environ, "OCR_PRELOAD")
        preload = configured_preload if configured_preload is not None else ocr_runner is run_rapidocr

    if preload_runner is None:
        preload_runner = get_engine

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if preload:
            started = time.perf_counter()
            await anyio.to_thread.run_sync(preload_runner)
            logger.info(
                "OCR model preloaded duration_ms=%.1f max_concurrency=%s",
                (time.perf_counter() - started) * 1000,
                max_concurrency,
            )
        yield

    app = FastAPI(title="Measure OCR Server", lifespan=lifespan)
    app.state.ocr_capacity = threading.BoundedSemaphore(max_concurrency)
    app.state.ocr_acquire_timeout_s = acquire_timeout_s
    app.state.ocr_max_concurrency = max_concurrency

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/measure/ocr")
    async def measure_ocr(
        request: Request,
        file: UploadFile | None = File(default=None),
        image: UploadFile | None = File(default=None),
    ) -> dict:
        started = time.perf_counter()
        image_input = await read_image_input(request, file or image)
        input_source = getattr(request.state, "ocr_input_source", "unknown")
        acquired = await acquire_ocr_capacity(request.app)
        if not acquired:
            logger.warning(
                "OCR capacity busy input=%s max_concurrency=%s acquire_timeout_s=%.3f",
                input_source,
                request.app.state.ocr_max_concurrency,
                request.app.state.ocr_acquire_timeout_s,
            )
            raise HTTPException(
                status_code=429,
                detail="OCR workers are busy. Please retry later.",
            )

        try:
            lines = await anyio.to_thread.run_sync(ocr_runner, image_input)
            line_count = len(lines) if hasattr(lines, "__len__") else 0
            result = parse_measure_ocr(lines)
            logger.info(
                "OCR request success input=%s duration_ms=%.1f lines=%s "
                "network=%s cell_id=%s signal=%s max_concurrency=%s %s",
                input_source,
                (time.perf_counter() - started) * 1000,
                line_count,
                result.get("network"),
                result.get("cell_id"),
                result.get("signal"),
                request.app.state.ocr_max_concurrency,
                format_ocr_profile(lines),
            )
            return result
        except Exception:
            logger.exception(
                "OCR request failed input=%s duration_ms=%.1f",
                input_source,
                (time.perf_counter() - started) * 1000,
            )
            raise
        finally:
            request.app.state.ocr_capacity.release()

    return app


async def acquire_ocr_capacity(app: FastAPI) -> bool:
    return await anyio.to_thread.run_sync(
        app.state.ocr_capacity.acquire,
        True,
        app.state.ocr_acquire_timeout_s,
    )


async def read_image_input(
    request: Request, upload: UploadFile | None = None
) -> ImageInput:
    if upload is not None:
        content = await upload.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded image is empty.")
        request.state.ocr_input_source = "upload"
        return content

    payload = await read_json_payload(request)

    image_base64 = payload.get("image_base64")
    if image_base64:
        request.state.ocr_input_source = "base64"
        return decode_base64_image(str(image_base64))

    image_path = payload.get("image_path")
    if image_path:
        request.state.ocr_input_source = "image_path"
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
