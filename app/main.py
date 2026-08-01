"""BYOS endpoints for the TRMNL device.

The device speaks three endpoints: /api/setup once during pairing, then
/api/display on every wake, plus /api/log for firmware diagnostics. Everything
else here exists for us, not for the device.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import string
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .refresh import ScreenState, refresh_screen
from .render import render_message, to_bmp_bytes, to_png_bytes
from .scheduler import build_scheduler, refresh_rate_for
from .store import Store

log = logging.getLogger(__name__)

FRIENDLY_ALPHABET = string.ascii_uppercase + string.digits


def _friendly_id() -> str:
    return "".join(secrets.choice(FRIENDLY_ALPHABET) for _ in range(6))


def _normalise_mac(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().upper()


def _as_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _as_int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    store = Store(settings.data_dir)
    state = ScreenState()

    # Persisted so the image URL survives a restart; the device would otherwise
    # keep asking for a path that no longer validates.
    token = settings.image_token or store.get_kv("image_token")
    if not token:
        token = secrets.token_urlsafe(32)
    store.set_kv("image_token", token)

    async def do_refresh() -> None:
        try:
            await refresh_screen(settings, store, state)
        except Exception:  # noqa: BLE001 - a scheduler job must never die silently
            log.exception("Refresh failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A device polling before the first fetch completes must still get a
        # valid image rather than a 404.
        if store.current_digest() is None:
            payload = to_bmp_bytes(render_message("trmnl-screen-1", ["Kalender wird geladen …"]))
            store.save_image(hashlib.sha1(payload).hexdigest()[:16], payload)

        task = asyncio.create_task(do_refresh())
        scheduler = build_scheduler(settings, do_refresh)
        scheduler.start()
        log.info(
            "Started. Fetch every %d min, device refresh %d s, quiet %02d:00-%02d:00",
            settings.fetch_interval_minutes,
            settings.display_refresh_seconds,
            settings.quiet_start_hour,
            settings.quiet_end_hour,
        )
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)
            task.cancel()

    app = FastAPI(title="trmnl-screen-1", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.screen = state
    app.state.image_token = token

    def base_url(request: Request) -> str:
        if settings.public_base_url:
            return settings.public_base_url.rstrip("/")
        if settings.railway_public_domain:
            return f"https://{settings.railway_public_domain}"
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        return f"{proto}://{host}" if host else str(request.base_url).rstrip("/")

    def image_url(request: Request) -> tuple[str, str]:
        digest = store.current_digest() or "pending"
        filename = f"{digest}.bmp"
        return f"{base_url(request)}/image/{token}/{filename}", filename

    # --- device endpoints -------------------------------------------------

    @app.api_route("/api/setup", methods=["GET", "POST"])
    async def api_setup(request: Request) -> JSONResponse:
        mac = _normalise_mac(request.headers.get("id"))
        if not mac:
            raise HTTPException(status_code=400, detail="Missing ID header")

        if settings.pairing_secret:
            supplied = request.headers.get("x-pairing-secret") or request.query_params.get("secret")
            device = store.get_device(mac)
            # A known device may re-run setup after a factory reset without the
            # secret; an unknown one may not pair without it.
            if device is None and not secrets.compare_digest(supplied or "", settings.pairing_secret):
                log.warning("Rejected pairing attempt from %s", mac)
                raise HTTPException(status_code=403, detail="Invalid pairing secret")

        device = store.get_device(mac)
        if device is None:
            device = store.create_device(mac, secrets.token_urlsafe(24), _friendly_id())
            log.info("Paired new device %s as %s", mac, device["friendly_id"])

        url, _ = image_url(request)
        return JSONResponse(
            {
                "status": 200,
                "api_key": device["api_key"],
                "friendly_id": device["friendly_id"],
                "image_url": url,
                "message": "Welcome to trmnl-screen-1",
            }
        )

    @app.api_route("/api/display", methods=["GET", "POST"])
    async def api_display(
        request: Request,
        id: str | None = Header(default=None),
        access_token: str | None = Header(default=None, alias="Access-Token"),
    ) -> JSONResponse:
        mac = _normalise_mac(id)
        if not mac:
            raise HTTPException(status_code=400, detail="Missing ID header")

        device = store.get_device(mac)
        if device is None or not access_token:
            raise HTTPException(status_code=401, detail="Unknown device")
        if not secrets.compare_digest(access_token, device["api_key"]):
            raise HTTPException(status_code=401, detail="Invalid access token")

        store.record_poll(
            mac,
            battery=_as_float(request.headers.get("battery-voltage")),
            rssi=_as_int(request.headers.get("rssi")),
            firmware=request.headers.get("fw-version"),
        )

        url, filename = image_url(request)
        return JSONResponse(
            {
                "status": 0,
                "image_url": url,
                "filename": filename,
                "refresh_rate": refresh_rate_for(datetime.now(settings.tz), settings),
                "image_url_timeout": 0,
                "update_firmware": False,
                "firmware_url": None,
                "reset_firmware": False,
                "special_function": "none",
            }
        )

    @app.post("/api/log")
    async def api_log(request: Request) -> Response:
        mac = _normalise_mac(request.headers.get("id")) or "unknown"
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - never fail a diagnostic upload
            body = {}
        for entry in body.get("logs", []) if isinstance(body, dict) else []:
            log.info(
                "device %s [%s] %s (fw=%s battery=%s wifi=%s wake=%s)",
                mac,
                entry.get("level", "info"),
                entry.get("message", ""),
                entry.get("firmware_version"),
                entry.get("battery_voltage"),
                entry.get("wifi_signal"),
                entry.get("wake_reason"),
            )
        return Response(status_code=204)

    # --- image delivery ---------------------------------------------------

    @app.get("/image/{supplied_token}/{filename}")
    async def serve_image(supplied_token: str, filename: str) -> Response:
        if not secrets.compare_digest(supplied_token, token):
            raise HTTPException(status_code=404, detail="Not found")

        digest = filename.removesuffix(".bmp")
        path = store.image_path(digest)
        if not path.exists():
            current = store.current_digest()
            if not current:
                raise HTTPException(status_code=404, detail="No screen rendered yet")
            path = store.image_path(current)
            if not path.exists():
                raise HTTPException(status_code=404, detail="No screen rendered yet")

        return Response(
            content=path.read_bytes(),
            media_type="image/bmp",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/preview/{supplied_token}.png")
    async def preview(supplied_token: str) -> Response:
        """Exactly what the device shows, as PNG for a browser."""
        if not secrets.compare_digest(supplied_token, token):
            raise HTTPException(status_code=404, detail="Not found")

        digest = store.current_digest()
        if not digest:
            raise HTTPException(status_code=404, detail="No screen rendered yet")

        from PIL import Image

        with Image.open(store.image_path(digest)) as image:
            return Response(content=to_png_bytes(image), media_type="image/png")

    @app.post("/preview/{supplied_token}/refresh")
    async def force_refresh(supplied_token: str) -> JSONResponse:
        if not secrets.compare_digest(supplied_token, token):
            raise HTTPException(status_code=404, detail="Not found")
        await do_refresh()
        return JSONResponse({"digest": state.digest, "events": state.event_count})

    # --- operations -------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        devices = store.list_devices()
        return JSONResponse(
            {
                "ok": True,
                "digest": state.digest,
                "generated_at": state.generated_at.isoformat() if state.generated_at else None,
                "events": state.event_count,
                "stale_calendars": state.stale_calendars,
                "last_error": state.last_error,
                "caldav_configured": settings.caldav_configured,
                "devices": [
                    {
                        "friendly_id": d["friendly_id"],
                        "last_seen": d["last_seen"],
                        "battery": d["last_battery"],
                        "rssi": d["last_rssi"],
                        "firmware": d["last_firmware"],
                    }
                    for d in devices
                ],
            }
        )

    return app
