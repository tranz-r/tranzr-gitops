#!/usr/bin/env python3
"""SEO-30: fail-closed checks that Helm emits default-off Seo__* on workerScheduler
and does not wire live SEO secret env refs until uncommented in values.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT
VALUES = ROOT / "values.yaml"
VALUES_STAGING = ROOT / "values-staging.yaml"

REQUIRED_PLAIN = [
    "Seo__Enabled",
    "Seo__ImportEnabled",
    "Seo__DiscoveryEnabled",
    "Seo__GenerationEnabled",
    "Seo__DataForSEO__Enabled",
    "Seo__DataForSEO__UseFake",
    "Seo__DeepSeek__Enabled",
    "Seo__DeepSeek__UseFake",
    "Seo__SearchConsole__Enabled",
    "Seo__SearchConsole__UseFake",
    "Seo__WordPress__Enabled",
    "Seo__WordPress__UseFake",
    "Seo__PostHogImport__Enabled",
    "Seo__PostHogImport__UseFake",
    "Seo__Operations__Paused",
]

FORBIDDEN_SECRET_ENV = [
    "Seo__DataForSEO__Login",
    "Seo__DataForSEO__Password",
    "Seo__DeepSeek__ApiKey",
    "Seo__SearchConsole__ServiceAccountJson",
    "Seo__WordPress__ApplicationPassword",
    "Seo__PostHogImport__PersonalApiKey",
]


class Failures:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def check(self, ok: bool, msg: str) -> None:
        if not ok:
            self.errors.append(msg)


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def helm_template(values_files: list[Path]) -> str:
    cmd = ["helm", "template", "seo-verify", str(CHART)]
    for vf in values_files:
        cmd.extend(["-f", str(vf)])
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"helm template failed:\n{proc.stderr or proc.stdout}")
    return proc.stdout


def extract_scheduler_env(rendered: str) -> dict[str, str]:
    """Pull env name/value pairs from the worker-scheduler Deployment manifest."""
    parts = re.split(r"^---\s*$", rendered, flags=re.M)
    block = None
    for part in parts:
        if "kind: Deployment" in part and "tranzr-moves-worker-scheduler" in part:
            block = part
            break
    if block is None:
        return {}

    env: dict[str, str] = {}
    # Match plain value env entries in the scheduler pod template.
    for m in re.finditer(
        r"- name: (Seo__[A-Za-z0-9_]+)\n\s+value: \"([^\"]*)\"",
        block,
    ):
        env[m.group(1)] = m.group(2)
    for m in re.finditer(
        r"- name: (Seo__[A-Za-z0-9_]+)\n\s+valueFrom:",
        block,
    ):
        env[m.group(1)] = "__secret_ref__"
    return env


def main() -> int:
    f = Failures()
    print("== SEO V1 GitOps verifier (SEO-30) ==")
    lint = run(["helm", "lint", str(CHART), "-f", str(VALUES), "-f", str(VALUES_STAGING)])
    f.check(lint.returncode == 0, f"helm lint failed:\n{lint.stdout}\n{lint.stderr}")

    try:
        rendered = helm_template([VALUES, VALUES_STAGING])
    except RuntimeError as ex:
        print("FAIL:", ex)
        return 1

    env = extract_scheduler_env(rendered)
    f.check(bool(env), "worker-scheduler Deployment Seo__* env not found in template")

    for key in REQUIRED_PLAIN:
        f.check(key in env, f"scheduler missing plain env {key}")
    f.check(env.get("Seo__Enabled") == "false", "Seo__Enabled must be false in base+staging")
    f.check(env.get("Seo__DataForSEO__UseFake") == "true", "DataForSEO UseFake must be true by default")
    f.check(env.get("Seo__DeepSeek__UseFake") == "true", "DeepSeek UseFake must be true by default")
    f.check(env.get("Seo__WordPress__UseFake") == "true", "WordPress UseFake must be true by default")
    f.check(env.get("Seo__SearchConsole__UseFake") == "true", "GSC UseFake must be true by default")
    f.check(env.get("Seo__PostHogImport__UseFake") == "true", "PostHogImport UseFake must be true by default")

    for key in FORBIDDEN_SECRET_ENV:
        val = env.get(key)
        # Plain empty placeholders from values.yaml are OK; secret refs / non-empty = live wire.
        f.check(
            val in (None, ""),
            f"{key} must stay empty/unwired until AKV secret exists (got {val!r})",
        )

    if f.errors:
        for err in f.errors:
            print("FAIL:", err)
        return 1
    print("OK: SEO Helm defaults are fail-closed (Enabled=false, UseFake=true, no live secret refs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
