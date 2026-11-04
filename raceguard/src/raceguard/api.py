"""FastAPI adapter for the working prototype."""

from __future__ import annotations

import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Annotated

try:
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import HTMLResponse
except ImportError as exc:  # pragma: no cover - depends on optional installation
    raise RuntimeError("API support requires: pip install -e '.[api]'") from exc

from .analysis import analyze
from .ingest import TelemetryError, load_csv, load_fit
from .reporting import result_to_dict

app = FastAPI(
    title="RaceGuard",
    version="0.1.0",
    description="Review-oriented telemetry screening; results are not adjudications.",
)

MAX_UPLOAD_BYTES = 4 * 1024 * 1024


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return files("raceguard").joinpath("static/index.html").read_text(encoding="utf-8")


@app.post("/api/analyze")
async def analyze_upload(
    uploads: Annotated[list[UploadFile], File(alias="file")],
    rider_ids: Annotated[list[str] | None, Form()] = None,
    rider_id: Annotated[str | None, Form()] = None,
) -> dict:
    if len(uploads) > 20:
        raise HTTPException(400, "Upload no more than 20 activity files at once")
    supplied_ids = rider_ids or ([rider_id] if rider_id else [])
    fit_index = 0
    uploaded_bytes = 0
    all_points = []
    for upload in uploads:
        filename = upload.filename or "telemetry.csv"
        suffix = Path(filename).suffix.lower()
        if suffix not in {".csv", ".fit"}:
            raise HTTPException(400, f"{filename}: upload a .csv or .fit file")
        content = await upload.read()
        if not content:
            raise HTTPException(400, f"{filename}: the uploaded file is empty")
        uploaded_bytes += len(content)
        if uploaded_bytes > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Combined upload exceeds the 4 MB deployment limit")

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
                temporary.write(content)
                temporary_path = Path(temporary.name)
            if suffix == ".fit":
                fallback_id = Path(filename).stem or f"rider-{fit_index + 1}"
                fit_rider_id = (
                    supplied_ids[fit_index].strip()
                    if fit_index < len(supplied_ids) and supplied_ids[fit_index].strip()
                    else fallback_id
                )
                all_points.extend(load_fit(temporary_path, fit_rider_id))
                fit_index += 1
            else:
                all_points.extend(load_csv(temporary_path))
        except TelemetryError as exc:
            raise HTTPException(422, f"{filename}: {exc}") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return result_to_dict(analyze(all_points))
