"""BYOS endpoints for the TRMNL device.

The device speaks three endpoints: /api/setup once during pairing, then
/api/display on every wake, plus /api/log for firmware diagnostics. Everything
else here exists for us, not for the device.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import string
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .firmware import update_due as firmware_update_due
from .refresh import Fetcher, ScreenState, refresh_screen
from .render import render_message, to_device_bytes, to_png_bytes
from .scheduler import build_scheduler, full_refresh_due, refresh_rate_for
from .store import Store
from .weather import fetch_forecast

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


def create_app(settings: Settings | None = None, fetch: Fetcher = fetch_forecast) -> FastAPI:
    """Build the app. ``fetch`` is the forecast source, swapped out in tests."""
    settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    store = Store(settings.data_dir, settings.image_format)
    state = ScreenState()

    # Persisted so the image URL survives a restart; the device would otherwise
    # keep asking for a path that no longer validates.
    token = settings.image_token or store.get_kv("image_token")
    if not token:
        token = secrets.token_urlsafe(32)
    store.set_kv("image_token", token)

    async def do_refresh() -> None:
        try:
            await refresh_screen(settings, store, state, fetch)
        except Exception:  # noqa: BLE001 - a scheduler job must never die silently
            log.exception("Refresh failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A device polling before the first fetch completes must still get a
        # valid image rather than a 404.
        if store.current_digest() is None:
            payload = to_device_bytes(
                render_message("Küchentisch-Wetter", ["Wetter wird geladen …"]), store.image_ext
            )
            store.save_image(hashlib.sha1(payload).hexdigest()[:16], payload)

        task = asyncio.create_task(do_refresh())
        scheduler = build_scheduler(settings, do_refresh)
        scheduler.start()
        # Logged in full because a deployment that misbehaves is almost always
        # a variable that did not arrive the way it was meant to.
        log.info(
            "Started. data_dir=%s writable=%s url_prefix=%r location=%.4f,%.4f day_switch=%02d:00 "
            "allowed_macs=%d lock_first_pairing=%s fetch_at=:%s refresh=%ds quiet=%02d-%02d tz=%s "
            "image=%s full_refresh_every=%d temp_profile=%s firmware_target=%s",
            settings.data_dir,
            os.access(settings.data_dir, os.W_OK),
            settings.url_prefix or "(none)",
            settings.latitude,
            settings.longitude,
            settings.day_switch_hour,
            len(settings.allowed_mac_set),
            settings.lock_after_first_pairing,
            settings.fetch_at_minutes,
            settings.display_refresh_seconds,
            settings.quiet_start_hour,
            settings.quiet_end_hour,
            settings.timezone,
            settings.image_format,
            settings.full_refresh_every,
            settings.temperature_profile,
            settings.firmware_target or "(none)",
        )
        log.info("Healthcheck on /healthz, detailed status on %s/status", settings.url_prefix)
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

    # Every route is mounted under URL_PREFIX. With a secret prefix the device
    # URL itself becomes the credential, which is the only shape of secret the
    # firmware can carry — it accepts a base URL and nothing else.
    router = APIRouter()

    def base_url(request: Request) -> str:
        if settings.public_base_url:
            return settings.public_base_url.rstrip("/")
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        return f"{proto}://{host}" if host else str(request.base_url).rstrip("/")

    def image_url(request: Request) -> tuple[str, str]:
        digest = store.current_digest() or "pending"
        filename = f"{digest}.{store.image_ext}"
        return f"{base_url(request)}{settings.url_prefix}/image/{token}/{filename}", filename

    def reject_pairing(mac: str, request: Request) -> str | None:
        """Why this MAC may not register as a new device, or None if it may.

        The firmware can only be handed a base URL, so it cannot send a custom
        header. That rules out a header secret as the primary defence and puts
        the weight on URL_PREFIX, the MAC allowlist and the first-pairing lock.
        """
        allowed = settings.allowed_mac_set
        if allowed and mac not in allowed:
            return "MAC not in ALLOWED_MACS"

        if settings.pairing_secret:
            supplied = request.headers.get("x-pairing-secret") or request.query_params.get("secret")
            if not secrets.compare_digest(supplied or "", settings.pairing_secret):
                return "invalid pairing secret"

        if settings.lock_after_first_pairing and store.list_devices():
            return "a device is already paired (LOCK_AFTER_FIRST_PAIRING)"

        return None

    # --- device endpoints -------------------------------------------------

    @router.api_route("/api/setup", methods=["GET", "POST"])
    async def api_setup(request: Request) -> JSONResponse:
        mac = _normalise_mac(request.headers.get("id"))
        if not mac:
            raise HTTPException(status_code=400, detail="Missing ID header")

        device = store.get_device(mac)
        if device is None:
            # A device that already paired may always re-run setup, e.g. after
            # a factory reset.
            reason = reject_pairing(mac, request)
            if reason:
                log.warning("Rejected pairing attempt from %s: %s", mac, reason)
                raise HTTPException(status_code=403, detail="Pairing refused")
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

    @router.api_route("/api/display", methods=["GET", "POST"])
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

        # Decide how the panel should be driven for this poll. The device only
        # redraws when the filename differs from the one it last showed, so the
        # full refresh has to ride along with a name it has not seen.
        now = datetime.now(settings.tz)
        changed = filename != device["last_filename"]
        full_refresh = full_refresh_due(
            changed=changed,
            draws_since_full=device["draws_since_full"] or 0,
            last_full_date=device["last_full_date"],
            today=now.date().isoformat(),
            every=settings.full_refresh_every,
        )
        if changed:
            store.record_redraw(
                mac, filename, full_refresh=full_refresh, today=now.date().isoformat()
            )
            log.info(
                "Serving %s to %s as a %s refresh",
                filename,
                device["friendly_id"],
                "full" if full_refresh else "partial",
            )

        # Firmware: offered only while the device reports an older version. The
        # firmware itself compares nothing and, told to update, would reflash the
        # same image on every poll.
        reported = request.headers.get("fw-version")
        if reported and device["last_firmware"] and reported != device["last_firmware"]:
            log.info(
                "%s now runs firmware %s (was %s)",
                device["friendly_id"],
                reported,
                device["last_firmware"],
            )
        last_offer = device["last_firmware_offer"]
        offer_update = firmware_update_due(
            reported=reported,
            target=settings.firmware_target,
            last_offer=datetime.fromisoformat(last_offer) if last_offer else None,
            now=now,
            retry_after=timedelta(hours=settings.firmware_retry_hours),
        )
        if offer_update:
            store.record_firmware_offer(mac, now)
            log.info(
                "Offering firmware %s to %s (reports %s)",
                settings.firmware_target,
                device["friendly_id"],
                reported,
            )

        return JSONResponse(
            {
                "status": 0,
                "image_url": url,
                "filename": filename,
                "refresh_rate": refresh_rate_for(now, settings),
                "image_url_timeout": 0,
                "update_firmware": offer_update,
                "firmware_url": settings.firmware_download_url if offer_update else None,
                "reset_firmware": False,
                "special_function": "none",
                # Misleadingly named: what it actually does is force the slow
                # black/white refresh instead of the silent partial one.
                "maximum_compatibility": full_refresh,
                "temperature_profile": settings.temperature_profile,
            }
        )

    @router.post("/api/log")
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

    @router.get("/image/{supplied_token}/{filename}")
    async def serve_image(supplied_token: str, filename: str) -> Response:
        if not secrets.compare_digest(supplied_token, token):
            raise HTTPException(status_code=404, detail="Not found")

        digest = filename.rsplit(".", 1)[0]
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
            media_type=f"image/{store.image_ext}",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/preview/{supplied_token}.png")
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

    @router.post("/preview/{supplied_token}/refresh")
    async def force_refresh(supplied_token: str) -> JSONResponse:
        if not secrets.compare_digest(supplied_token, token):
            raise HTTPException(status_code=404, detail="Not found")
        await do_refresh()
        return JSONResponse({"digest": state.digest, "shown_days": state.shown_days})

    # --- operations -------------------------------------------------------

    @router.get("/status")
    async def status() -> JSONResponse:
        devices = store.list_devices()
        return JSONResponse(
            {
                "ok": True,
                "digest": state.digest,
                "generated_at": state.generated_at.isoformat() if state.generated_at else None,
                "shown_days": state.shown_days,
                "forecast_fetched_at": (
                    state.forecast_fetched_at.isoformat() if state.forecast_fetched_at else None
                ),
                "last_error": state.last_error,
                "location": {"latitude": settings.latitude, "longitude": settings.longitude},
                "day_switch_hour": settings.day_switch_hour,
                "image_format": settings.image_format,
                "full_refresh_every": settings.full_refresh_every,
                "temperature_profile": settings.temperature_profile,
                "firmware_target": settings.firmware_target or None,
                "devices": [
                    {
                        "friendly_id": d["friendly_id"],
                        "last_seen": d["last_seen"],
                        "battery": d["last_battery"],
                        "rssi": d["last_rssi"],
                        "firmware": d["last_firmware"],
                        "draws_since_full_refresh": d["draws_since_full"],
                        "last_full_refresh": d["last_full_date"],
                        "last_firmware_offer": d["last_firmware_offer"],
                    }
                    for d in devices
                ],
            }
        )

    # The platform healthcheck has to reach a fixed path, so it cannot live
    # behind URL_PREFIX. It deliberately says nothing beyond "the process is
    # up" — the detailed view at <prefix>/status exposes forecast and device
    # data and stays behind the secret.
    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    app.include_router(router, prefix=settings.url_prefix)
    return app
