"""HTTP health endpoint for Coolify/k8s liveness probes.

Returns 200 + JSON when the worker process is reachable; that's the operational
contract a liveness probe needs ("is the process responsive at all?"). Deeper
readiness checks (LK Cloud connectivity, plugin warmups) live in the agent
prewarm path — if those fail, the worker doesn't accept jobs and Coolify will
restart it via the same probe.
"""

from __future__ import annotations

import logging
import os
import time

from aiohttp import web

logger = logging.getLogger("agent")

_started_at = time.time()


def _make_app(active_sessions_getter) -> web.Application:
    async def health(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "uptime_s": round(time.time() - _started_at, 1),
                "active_sessions": active_sessions_getter(),
            }
        )

    app = web.Application()
    app.router.add_get("/health", health)
    return app


async def start_health_server(active_sessions_getter, port: int | None = None) -> web.AppRunner:
    """Spawn the aiohttp app on the worker process. Idempotent on re-start."""
    port = port or int(os.getenv("HEALTH_PORT", "8080"))
    app = _make_app(active_sessions_getter)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info(f"health endpoint listening on :{port}/health")
    return runner
