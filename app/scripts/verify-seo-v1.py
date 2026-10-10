#!/usr/bin/env python3
"""SEO-30: fail-closed checks for SEO Helm wiring.

- Base values.yaml: master off, UseFake true, no live secret env refs.
- Base + staging: Fake Gate D may enable master/stages/providers while UseFake
  stays true and secrets stay unwired.
- Production: live master + providers (UseFake false) with SEO AKV secret refs
  and non-secret SiteUrl / WordPress BaseUrl+Username set.
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
VALUES_PRODUCTION = ROOT / "values-production.yaml"

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

USE_FAKE_KEYS = [
    "Seo__DataForSEO__UseFake",
    "Seo__DeepSeek__UseFake",
    "Seo__WordPress__UseFake",
    "Seo__SearchConsole__UseFake",
    "Seo__PostHogImport__UseFake",
]

PROVIDER_ENABLED_KEYS = (
    "Seo__DataForSEO__Enabled",
    "Seo__DeepSeek__Enabled",
    "Seo__SearchConsole__Enabled",
    "Seo__WordPress__Enabled",
    "Seo__PostHogImport__Enabled",
)


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


def assert_no_live_secrets(f: Failures, env: dict[str, str], label: str) -> None:
    for key in FORBIDDEN_SECRET_ENV:
        val = env.get(key)
        f.check(
            val in (None, ""),
            f"{label}: {key} must stay empty/unwired until AKV secret exists (got {val!r})",
        )


def assert_use_fake(f: Failures, env: dict[str, str], label: str, *, expected: str = "true") -> None:
    for key in USE_FAKE_KEYS:
        f.check(env.get(key) == expected, f"{label}: {key} must be {expected}")


def assert_required_plain(f: Failures, env: dict[str, str], label: str) -> None:
    f.check(bool(env), f"{label}: worker-scheduler Deployment Seo__* env not found")
    for key in REQUIRED_PLAIN:
        f.check(key in env, f"{label}: scheduler missing plain env {key}")


def main() -> int:
    f = Failures()
    print("== SEO V1 GitOps verifier (SEO-30) ==")

    lint = run(
        ["helm", "lint", str(CHART), "-f", str(VALUES), "-f", str(VALUES_STAGING)]
    )
    f.check(lint.returncode == 0, f"helm lint failed:\n{lint.stdout}\n{lint.stderr}")

    try:
        base = extract_scheduler_env(helm_template([VALUES]))
        staging = extract_scheduler_env(helm_template([VALUES, VALUES_STAGING]))
        production = extract_scheduler_env(helm_template([VALUES, VALUES_PRODUCTION]))
    except RuntimeError as ex:
        print("FAIL:", ex)
        return 1

    assert_required_plain(f, base, "base")
    assert_use_fake(f, base, "base")
    assert_no_live_secrets(f, base, "base")
    f.check(base.get("Seo__Enabled") == "false", "base: Seo__Enabled must be false")
    f.check(base.get("Seo__DataForSEO__Enabled") == "false", "base: DataForSEO Enabled must be false")

    assert_required_plain(f, staging, "staging")
    assert_use_fake(f, staging, "staging")
    assert_no_live_secrets(f, staging, "staging")
    # Fake Gate D: master on, providers Enabled, still Fake, no AKV.
    f.check(staging.get("Seo__Enabled") == "true", "staging Fake Gate D: Seo__Enabled must be true")
    f.check(staging.get("Seo__ImportEnabled") == "true", "staging: Seo__ImportEnabled must be true")
    f.check(staging.get("Seo__DiscoveryEnabled") == "true", "staging: Seo__DiscoveryEnabled must be true")
    f.check(staging.get("Seo__GenerationEnabled") == "true", "staging: Seo__GenerationEnabled must be true")
    for key in PROVIDER_ENABLED_KEYS:
        f.check(staging.get(key) == "true", f"staging Fake Gate D: {key} must be true")

    assert_required_plain(f, production, "production")
    assert_use_fake(f, production, "production", expected="false")
    f.check(production.get("Seo__Enabled") == "true", "production live: Seo__Enabled must be true")
    f.check(production.get("Seo__ImportEnabled") == "true", "production live: Seo__ImportEnabled must be true")
    f.check(production.get("Seo__DiscoveryEnabled") == "true", "production live: Seo__DiscoveryEnabled must be true")
    f.check(production.get("Seo__GenerationEnabled") == "true", "production live: Seo__GenerationEnabled must be true")
    for key in PROVIDER_ENABLED_KEYS:
        f.check(production.get(key) == "true", f"production live: {key} must be true")
    f.check(
        production.get("Seo__SearchConsole__SiteUrl") == "sc-domain:tranzzer.com",
        "production live: Seo__SearchConsole__SiteUrl must be sc-domain:tranzzer.com",
    )
    f.check(
        production.get("Seo__WordPress__BaseUrl") == "https://tranzzer.com",
        "production live: Seo__WordPress__BaseUrl must be https://tranzzer.com",
    )
    f.check(
        bool(production.get("Seo__WordPress__Username")),
        "production live: Seo__WordPress__Username must be set",
    )
    f.check(
        production.get("Seo__PostHogImport__ProjectId") == "279484",
        "production live: Seo__PostHogImport__ProjectId must be set",
    )
    for key in FORBIDDEN_SECRET_ENV:
        f.check(
            production.get(key) == "__secret_ref__",
            f"production: {key} must be wired from AKV (got {production.get(key)!r})",
        )

    if f.errors:
        for err in f.errors:
            print("FAIL:", err)
        return 1
    print(
        "OK: base dark; staging Fake Gate D (Enabled=true, UseFake=true, no secrets); "
        "production live (Enabled=true, UseFake=false, AKV secret refs + SiteUrl/WP BaseUrl)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
