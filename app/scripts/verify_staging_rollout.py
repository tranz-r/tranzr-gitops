#!/usr/bin/env python3
"""Fail-closed Argo CD verification for the staging VisionNormalizer rollout."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

APP_NAME = "tranzr-moves-staging"
NAMESPACE = "tranzr-moves-staging"
NORMALIZER_NAME = "tranzr-moves-worker-vision-normalizer"


def staging_moves_version(values_text: str) -> str:
    in_images = False
    for line in values_text.splitlines():
        if line and not line[0].isspace():
            in_images = line.strip() == "images:"
            continue
        if in_images and line.startswith("  movesVersion:"):
            value = line.split(":", 1)[1].strip().strip("\"'")
            if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
                return value
            break
    raise ValueError("staging images.movesVersion is missing or invalid")


def _normalizer_resource(application: dict[str, Any]) -> dict[str, Any] | None:
    resources = ((application.get("status") or {}).get("resources") or [])
    for resource in resources:
        if (
            resource.get("group") == "apps"
            and resource.get("kind") == "Deployment"
            and resource.get("namespace") == NAMESPACE
            and resource.get("name") == NORMALIZER_NAME
        ):
            return resource
    return None


def application_errors(
    application: dict[str, Any], expected_revision: str
) -> list[str]:
    status = application.get("status") or {}
    sync = status.get("sync") or {}
    health = status.get("health") or {}
    errors: list[str] = []

    revision = sync.get("revision")
    if revision != expected_revision:
        errors.append(
            f"application revision is {revision!r}, expected {expected_revision!r}"
        )
    if sync.get("status") != "Synced":
        errors.append(
            f"application sync is {sync.get('status')!r}, expected 'Synced'"
        )
    if health.get("status") != "Healthy":
        errors.append(
            f"application health is {health.get('status')!r}, expected 'Healthy'"
        )

    normalizer = _normalizer_resource(application)
    if normalizer is None:
        errors.append("normalizer Deployment is absent from application resources")
    else:
        if normalizer.get("status") != "Synced":
            errors.append(
                f"normalizer sync is {normalizer.get('status')!r}, expected 'Synced'"
            )
        normalizer_health = (normalizer.get("health") or {}).get("status")
        if normalizer_health != "Healthy":
            errors.append(
                f"normalizer health is {normalizer_health!r}, expected 'Healthy'"
            )
    return errors


def _live_manifest(resource: dict[str, Any]) -> dict[str, Any]:
    manifest = resource.get("manifest") or ""
    if isinstance(manifest, str):
        parsed = json.loads(manifest)
        return parsed if isinstance(parsed, dict) else {}
    return manifest if isinstance(manifest, dict) else {}


def normalizer_errors(
    resource: dict[str, Any], expected_image: str
) -> list[str]:
    live = _live_manifest(resource)
    spec = live.get("spec") or {}
    pod = ((spec.get("template") or {}).get("spec") or {})
    containers = pod.get("containers") or []
    worker = next(
        (container for container in containers if container.get("name") == "worker"),
        None,
    )
    if worker is None:
        return ["normalizer live Deployment has no worker container"]

    errors: list[str] = []
    if spec.get("replicas") != 1:
        errors.append(f"normalizer replicas is {spec.get('replicas')!r}, expected 1")
    if worker.get("image") != expected_image:
        errors.append(
            f"normalizer image is {worker.get('image')!r}, expected {expected_image!r}"
        )

    env = {
        item.get("name"): item.get("value")
        for item in (worker.get("env") or [])
        if isinstance(item, dict) and "value" in item
    }
    if env.get("Worker__Role") != "VisionNormalizer":
        errors.append(
            f"normalizer role is {env.get('Worker__Role')!r}, expected 'VisionNormalizer'"
        )
    if env.get("Vision__Normalization__IntakeMode") != "LegacySync":
        errors.append(
            "normalizer intake mode is "
            f"{env.get('Vision__Normalization__IntakeMode')!r}, expected 'LegacySync'"
        )

    memory_limit = (((worker.get("resources") or {}).get("limits") or {}).get("memory"))
    if memory_limit != "1Gi":
        errors.append(
            f"normalizer memory limit is {memory_limit!r}, expected '1Gi'"
        )

    live_status = live.get("status") or {}
    if live_status.get("updatedReplicas") != 1:
        errors.append(
            "normalizer updatedReplicas is "
            f"{live_status.get('updatedReplicas')!r}, expected 1"
        )
    if live_status.get("readyReplicas") != 1:
        errors.append(
            f"normalizer readyReplicas is {live_status.get('readyReplicas')!r}, expected 1"
        )
    return errors


class ArgoClient:
    def __init__(self, server: str, username: str, password: str) -> None:
        self.server = server.rstrip("/")
        self.context = ssl.create_default_context()
        self.token = self._login(username, password)

    def _request(
        self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if hasattr(self, "token"):
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self.server}{path}", data=body, headers=headers, method=method
        )
        with urllib.request.urlopen(request, timeout=30, context=self.context) as response:
            decoded = json.load(response)
        if not isinstance(decoded, dict):
            raise RuntimeError("Argo CD returned a non-object JSON response")
        return decoded

    def _login(self, username: str, password: str) -> str:
        response = self._request(
            "/api/v1/session",
            method="POST",
            payload={"username": username, "password": password},
        )
        token = response.get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Argo CD login returned no session token")
        return token

    def application(self) -> dict[str, Any]:
        return self._request(f"/api/v1/applications/{APP_NAME}")

    def normalizer(self) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "namespace": NAMESPACE,
                "resourceName": NORMALIZER_NAME,
                "version": "v1",
                "kind": "Deployment",
                "group": "apps",
            }
        )
        return self._request(f"/api/v1/applications/{APP_NAME}/resource?{query}")


def verify_live(
    client: ArgoClient,
    expected_revision: str,
    expected_image: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    last_errors: list[str] = ["verification has not run"]
    while time.monotonic() < deadline:
        try:
            application = client.application()
            last_errors = application_errors(application, expected_revision)
            if not last_errors:
                resource = client.normalizer()
                last_errors = normalizer_errors(resource, expected_image)
            if not last_errors:
                print(
                    "staging rollout verified: "
                    f"revision={expected_revision} image={expected_image} "
                    "normalizer=Synced/Healthy/Ready LegacySync"
                )
                return 0
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_errors = [f"Argo CD query failed: {type(exc).__name__}"]
        print("waiting for staging rollout: " + "; ".join(last_errors))
        time.sleep(poll_seconds)

    print("staging rollout verification timed out: " + "; ".join(last_errors), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-image")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()

    password = os.environ.get("ARGOCD_ADMIN_PASSWORD")
    if not password:
        print("ARGOCD_ADMIN_PASSWORD is required", file=sys.stderr)
        return 2
    server = os.environ.get("ARGOCD_SERVER", "https://argocd.labgrid.net")
    username = os.environ.get("ARGOCD_USERNAME", "admin")

    try:
        client = ArgoClient(server, username, password)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"Argo CD login failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    expected_image = args.expected_image
    if not expected_image:
        try:
            values_text = (
                pathlib.Path(__file__).resolve().parent.parent / "values-staging.yaml"
            ).read_text()
            version = staging_moves_version(values_text)
        except (OSError, ValueError) as exc:
            print(f"Could not resolve staging image version: {type(exc).__name__}", file=sys.stderr)
            return 2
        expected_image = f"ghcr.io/tranz-r/tranzr-moves-worker:{version}"

    return verify_live(
        client,
        args.expected_revision,
        expected_image,
        args.timeout_seconds,
        args.poll_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
