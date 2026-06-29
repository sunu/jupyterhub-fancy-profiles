"""Prometheus metrics for profile-aware spawn timing.

Emits ``jupyterhub_fancy_profiles_spawn_duration_seconds`` alongside the
built-in ``jupyterhub_server_spawn_duration_seconds``, labeled with the
profile slug and (by default) the ``image`` / ``resources`` choices from
``spawner.user_options``.
"""

import time

from kubespawner import KubeSpawner
from prometheus_client import Gauge, Histogram

# Mirrors the bucket layout JupyterHub uses for server_spawn_duration_seconds,
# since pod spawns operate on the same time scale.
_SPAWN_DURATION_BUCKETS = [
    0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 180.0, 300.0, 600.0,
]

_PATCHED_ATTR = "_fancy_profiles_metrics_patched"

PROFILE_SPAWN_DURATION_SECONDS = None
LAST_SPAWN_DURATION_SECONDS = None


def setup_metrics(option_labels=("image", "resources")):
    """Wrap ``KubeSpawner.start`` to record per-profile spawn durations.

    ``option_labels`` lists the ``user_options`` keys to project as extra
    Prometheus labels; missing keys for a given spawn render as ``"none"``
    so the label set stays stable. Safe to call multiple times.
    """
    global PROFILE_SPAWN_DURATION_SECONDS, LAST_SPAWN_DURATION_SECONDS
    if getattr(KubeSpawner, _PATCHED_ATTR, False):
        return

    option_labels = tuple(option_labels)
    label_keys = ("status", "profile", *option_labels)
    PROFILE_SPAWN_DURATION_SECONDS = Histogram(
        "jupyterhub_fancy_profiles_spawn_duration_seconds",
        "Time taken by Spawner.start, labeled with profile and selected options",
        label_keys,
        buckets=_SPAWN_DURATION_BUCKETS,
    )
    # Gauge holding the most recent spawn duration per label combo, so
    # min_over_time / max_over_time give exact values rather than the
    # bucket-rounded approximations a histogram provides.
    LAST_SPAWN_DURATION_SECONDS = Gauge(
        "jupyterhub_fancy_profiles_last_spawn_duration_seconds",
        "Duration of the most recent Spawner.start per profile/option combination",
        label_keys,
    )

    original_start = KubeSpawner.start

    async def start(self):
        t0 = time.perf_counter()
        status = "failure"
        try:
            result = await original_start(self)
            status = "success"
            return result
        finally:
            opts = self.user_options or {}
            labels = {
                "status": status,
                "profile": opts.get("profile") or "unknown",
            }
            for key in option_labels:
                labels[key] = opts.get(key) or "none"
            elapsed = time.perf_counter() - t0
            PROFILE_SPAWN_DURATION_SECONDS.labels(**labels).observe(elapsed)
            LAST_SPAWN_DURATION_SECONDS.labels(**labels).set(elapsed)

    KubeSpawner.start = start
    setattr(KubeSpawner, _PATCHED_ATTR, True)
