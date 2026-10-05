#!/usr/bin/env python3
"""Repository-local Vision V1 / Photo Inventory GitOps verifier.

Renders Helm manifests for default/shared, staging, production (common photo
pin / native AsyncQueue / five API MIME), and a temporary messaging
rollback/dark override (no secrets / no cluster). Exit 0 only when all
assertions pass.

Requires VisionModeActuator Job + transition startup fields to be absent.
Ordinary schema migrator Helm→PreSync hooks and the dedicated VisionNormalizer
AsyncQueue workload must remain. Shared pin is published moves 0.122.8
(VisionModeActuator role retired).
"""

from __future__ import annotations

import copy
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "app"
VALUES_DEFAULT = CHART / "values.yaml"
VALUES_STAGING = CHART / "values-staging.yaml"
VALUES_PRODUCTION = CHART / "values-production.yaml"


def _load_default_values() -> dict[str, Any]:
    data = yaml.safe_load(VALUES_DEFAULT.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"values.yaml did not parse as a mapping: {VALUES_DEFAULT}")
    return data


def _shared_moves_version(values: dict[str, Any]) -> str:
    version = str(((values.get("images") or {}).get("movesVersion") or "")).strip()
    if not version:
        raise RuntimeError("images.movesVersion missing from values.yaml")
    return version


# Hard release pin — must match images.movesVersion (workloads + migrators).
SHARED_MOVES_VERSION = "0.122.8"

_DEFAULT_VALUES = _load_default_values()
_VALUES_MOVES_VERSION = _shared_moves_version(_DEFAULT_VALUES)
if _VALUES_MOVES_VERSION != SHARED_MOVES_VERSION:
    raise RuntimeError(
        f"images.movesVersion {_VALUES_MOVES_VERSION!r} != release pin "
        f"{SHARED_MOVES_VERSION!r}"
    )
if (_DEFAULT_VALUES.get("jobs") or {}).get("visionModeActivation") is not None:
    raise RuntimeError(
        "jobs.visionModeActivation must be removed from values.yaml "
        "(VisionModeActuator retired)"
    )
ACTIVATION_JOB_NAME_SUBSTR = "vision-mode-activation"
FORBIDDEN_ACTUATOR_ENV = (
    "Vision__Normalization__TransitionFromMode",
    "Vision__Normalization__TransitionOperationId",
    "Vision__Normalization__DeploymentIdentity",
    "Vision__Normalization__ActivationDrainBudgetSeconds",
    "Vision__Normalization__ActivationDrainPollSeconds",
)
# History/enum vocabulary only (LegacySync unsupported as active intake;
# IntakePaused = operational pause; AsyncQueue = current path). Not Jobs.
DURABLE_INTAKE_MODE_NAMES = ("LegacySync", "IntakePaused", "AsyncQueue")

ARGO_HOOK_ANNOTATION = "argocd.argoproj.io/hook"
ARGO_HOOK_DELETE_POLICY = "argocd.argoproj.io/hook-delete-policy"
ARGO_SYNC_WAVE = "argocd.argoproj.io/sync-wave"
HELM_HOOK_ANNOTATION = "helm.sh/hook"
HELM_HOOK_WEIGHT = "helm.sh/hook-weight"
HELM_HOOK_DELETE_POLICY = "helm.sh/hook-delete-policy"

# Official Argo CD Helm→Argo mapping (docs/user-guide/helm.md#helm-hooks).
# If ANY resource has argocd.argoproj.io/hook, Argo ignores ALL Helm hooks.
HELM_HOOK_TO_ARGO_PHASE = {
    "pre-install": "PreSync",
    "pre-upgrade": "PreSync",
    "post-install": "PostSync",
    "post-upgrade": "PostSync",
    "pre-delete": "PreDelete",
    "post-delete": "PostDelete",
}
HELM_DELETE_TO_ARGO = {
    "before-hook-creation": "BeforeHookCreation",
    "hook-succeeded": "HookSucceeded",
    "hook-failed": "HookFailed",
}
# Unsupported by Argo (skipped); still must not mix with explicit Argo hooks.
HELM_HOOKS_UNSUPPORTED = frozenset({"test", "test-success", "test-failure", "crd-install"})

# Expected PreSync helm weights: secrets → ordinary schema migrators (no actuator).
# Keys are logical roles resolved via kind/name/component (not raw substrings).
EXPECTED_PRESYNC_WEIGHTS = {
    "serviceaccount": "-10",
    "imagepull": "-9",
    "application-secrets": "-8",
    "db-migration": "0",
    "notifications-db-migration": "1",
}

OPENROUTER_SECRET_KEY = "tranzr-openrouter-api-key"
AZURE_STORAGE_SECRET_KEY = "tranzr-azure-storage-connection-string"
NORMALIZATION_QUEUE = "vision-normalization-v1"
NORMALIZATION_SPOOL_DIRECTORY = "/var/spool/tranzr/vision-normalization"
NORMALIZATION_SPOOL_QUOTA_BYTES = "2147483648"
NORMALIZATION_SPOOL_HEADROOM_BYTES = "536870912"
NORMALIZATION_ENV_PREFIX = "Vision__Normalization__"
MEDIA_NORMALIZATION_POLICY_KEY = "Vision__Media__NormalizationPolicyVersion"
NORMALIZATION_POLICY_KEY = "Vision__Normalization__NormalizationPolicyVersion"
ALLOWED_CONTENT_TYPES_ENV_PREFIX = "Vision__Media__AllowedContentTypes__"
# Shared API native async advertisement allowlist (JPEG/PNG/WebP + still-HEVC aliases).
API_ALLOWED_CONTENT_TYPES = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
)
# Drift-only fixture for fail-closed mutation self-test (not an expected runtime pin).
LEGACY_NORMALIZATION_POLICY_VERSION = "norm-v1-jpeg-q85-s420"
# 0.122.5+ matches VipsVisionNormalizerOptions.ProductionNormalizationPolicyVersion.
NATIVE_NORMALIZATION_POLICY_VERSION = "norm-v3-libvips-8.18.7-jpeg-q85-s420-srgb"

OPENROUTER_BOUNDS = {
    "Vision__OpenRouter__BaseUrl": "https://openrouter.ai",
    "Vision__OpenRouter__TimeoutSeconds": "45",
    "Vision__OpenRouter__MaxTokens": "2000",
    "Vision__OpenRouter__MaxResponseBodyBytes": "262144",
    "Vision__OpenRouter__MaxConcurrentRequests": "4",
}

# Category-aware inference is on; candidate learning / admin review / manufacturer lookup stay dark.
CATALOGUE_EXPECTED = {
    "Vision__CatalogueLearning__CategoryAwareInferenceEnabled": "true",
    "Vision__CatalogueLearning__CandidateLearningEnabled": "false",
    "Vision__CatalogueLearning__AdminReviewEnabled": "false",
    "Vision__CatalogueLearning__ManufacturerLookupEnabled": "false",
}

# API-only gate for mandatory customer confirmation; must not appear on worker/scheduler.
CONFIRMATION_ACTIVATION_KEY = "Vision__ConfirmationReview__ActivationEnabled"


class Failures(list[str]):
    def check(self, cond: bool, msg: str) -> None:
        if not cond:
            self.append(msg)


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def helm_template(
    release: str,
    values_files: list[Path],
    *,
    set_yaml: str | None = None,
    extra_values_file: Path | None = None,
) -> str:
    cmd = ["helm", "template", release, str(CHART)]
    for vf in values_files:
        cmd.extend(["-f", str(vf)])
    if extra_values_file is not None:
        cmd.extend(["-f", str(extra_values_file)])
    if set_yaml:
        cmd.extend(["--set-json", set_yaml] if set_yaml.startswith("{") else ["--set", set_yaml])
    proc = run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"helm template failed ({' '.join(cmd)}):\n{proc.stderr or proc.stdout}"
        )
    return proc.stdout


def helm_lint(values_files: list[Path]) -> None:
    cmd = ["helm", "lint", str(CHART)]
    for vf in values_files:
        cmd.extend(["-f", str(vf)])
    proc = run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"helm lint failed ({' '.join(cmd)}):\n{proc.stderr or proc.stdout}")


def load_docs(rendered: str) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for doc in yaml.safe_load_all(rendered):
        if isinstance(doc, dict):
            docs.append(doc)
    return docs


def find_docs(docs: list[dict[str, Any]], kind: str, name_substr: str) -> list[dict[str, Any]]:
    out = []
    for d in docs:
        if d.get("kind") != kind:
            continue
        name = (d.get("metadata") or {}).get("name", "")
        if name_substr in name:
            out.append(d)
    return out


def container_env(doc: dict[str, Any]) -> dict[str, Any]:
    """Map env name -> either literal value or secretKeyRef dict."""
    containers = (
        ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    ).get("containers") or []
    if not containers:
        return {}
    env_list = containers[0].get("env") or []
    result: dict[str, Any] = {}
    for item in env_list:
        name = item.get("name")
        if not name:
            continue
        if "value" in item:
            result[name] = item.get("value")
        elif "valueFrom" in item:
            result[name] = (item.get("valueFrom") or {}).get("secretKeyRef") or item["valueFrom"]
    return result


def vision_env_contract(doc: dict[str, Any]) -> dict[str, Any]:
    """Exact Vision__* env name -> {value} or {valueFrom} mapping for contract compare."""
    containers = (
        ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    ).get("containers") or []
    if not containers:
        return {}
    env_list = containers[0].get("env") or []
    result: dict[str, Any] = {}
    for item in env_list:
        name = item.get("name")
        if not name or not str(name).startswith("Vision__"):
            continue
        entry: dict[str, Any] = {}
        if "value" in item:
            entry["value"] = item.get("value")
        if "valueFrom" in item:
            entry["valueFrom"] = item.get("valueFrom")
        result[name] = entry
    return result


def container_image(doc: dict[str, Any]) -> str:
    containers = (
        ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    ).get("containers") or []
    if not containers:
        return ""
    return containers[0].get("image") or ""


def pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    return (((doc.get("spec") or {}).get("template") or {}).get("spec") or {})


def first_container(doc: dict[str, Any]) -> dict[str, Any]:
    containers = pod_spec(doc).get("containers") or []
    return containers[0] if containers else {}


def normalization_env(env: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in env.items() if key.startswith(NORMALIZATION_ENV_PREFIX)}


def _split_csv_annotation(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def doc_annotations(doc: dict[str, Any]) -> dict[str, Any]:
    return (doc.get("metadata") or {}).get("annotations") or {}


def resources_with_explicit_argo_hooks(
    docs: list[dict[str, Any]],
) -> list[tuple[str, str, str]]:
    """Return (kind, name, hook) for every explicit Argo hook annotation."""
    found: list[tuple[str, str, str]] = []
    for doc in docs:
        ann = doc_annotations(doc)
        hook = ann.get(ARGO_HOOK_ANNOTATION)
        if not hook:
            continue
        meta = doc.get("metadata") or {}
        found.append((str(doc.get("kind") or ""), str(meta.get("name") or ""), str(hook)))
    return found


def effective_argo_hook_mapping(
    annotations: dict[str, Any],
) -> tuple[str | None, str | None, set[str]]:
    """Resolve Helm hooks → Argo phase / wave / delete-policy when mapper is active.

    Returns (phase, sync_wave, delete_policies). phase is None when hooks are
    unsupported/empty; mixed supported phases are joined with '+'.
    """
    helm_hooks = _split_csv_annotation(annotations.get(HELM_HOOK_ANNOTATION))
    phases: list[str] = []
    for hook in helm_hooks:
        if hook in HELM_HOOKS_UNSUPPORTED:
            continue
        mapped = HELM_HOOK_TO_ARGO_PHASE.get(hook)
        if mapped and mapped not in phases:
            phases.append(mapped)
    phase = "+".join(phases) if phases else None
    weight = annotations.get(HELM_HOOK_WEIGHT)
    wave = str(weight) if weight is not None and str(weight) != "" else None
    delete_policies = {
        HELM_DELETE_TO_ARGO[part]
        for part in _split_csv_annotation(annotations.get(HELM_HOOK_DELETE_POLICY))
        if part in HELM_DELETE_TO_ARGO
    }
    return phase, wave, delete_policies


def assert_helm_only_hook_mapping_active(
    f: Failures, label: str, docs: list[dict[str, Any]]
) -> bool:
    """Fail closed if any explicit Argo hook would disable Helm hook mapping."""
    explicit = resources_with_explicit_argo_hooks(docs)
    f.check(
        not explicit,
        f"{label}: BLOCKER — explicit {ARGO_HOOK_ANNOTATION} disables ALL Helm "
        f"hooks (Argo docs/user-guide/helm.md); migrate every prereq+dependency "
        f"to Argo hooks instead of relying on the mapper. Found: {explicit!r}",
    )
    return not explicit


def _presync_role(doc: dict[str, Any]) -> str | None:
    """Map a PreSync resource to a logical ordering role, or None if unrelated."""
    kind = str(doc.get("kind") or "")
    name = str((doc.get("metadata") or {}).get("name") or "")
    component = str(
        ((doc.get("metadata") or {}).get("labels") or {}).get(
            "app.kubernetes.io/component"
        )
        or ""
    )
    if kind == "ServiceAccount":
        return "serviceaccount"
    if kind == "ExternalSecret" and (
        "github-registry" in name or component == "github-registry-secret"
    ):
        return "imagepull"
    if kind == "ExternalSecret" and (
        name.endswith("tranzrmoves-secrets")
        or "application" in component
        or component == "tranzrmoves-secrets"
    ):
        return "application-secrets"
    if kind == "Job" and ACTIVATION_JOB_NAME_SUBSTR in name:
        return "activation"
    if kind == "Job" and "notifications-db-migration" in name:
        return "notifications-db-migration"
    if kind == "Job" and name.endswith("-db-migration"):
        return "db-migration"
    return None


def assert_presync_weight_ordering(
    f: Failures, label: str, docs: list[dict[str, Any]]
) -> None:
    """Assert Helm PreSync weights: secrets -10..-8 → migrators 0/1 (no actuator)."""
    role_to_weight: dict[str, str] = {}
    for doc in docs:
        ann = doc_annotations(doc)
        phase, wave, _ = effective_argo_hook_mapping(ann)
        if phase != "PreSync" or wave is None:
            continue
        role = _presync_role(doc)
        if role is None:
            continue
        # Prefer the first match; duplicates with conflicting weights fail below.
        if role in role_to_weight and role_to_weight[role] != wave:
            f.check(
                False,
                f"{label}: conflicting PreSync weights for {role!r}: "
                f"{role_to_weight[role]!r} vs {wave!r}",
            )
        role_to_weight[role] = wave

    f.check(
        "activation" not in role_to_weight,
        f"{label}: PreSync must not include VisionModeActuator activation role "
        f"(got weight {role_to_weight.get('activation')!r})",
    )

    for key, expected in EXPECTED_PRESYNC_WEIGHTS.items():
        got = role_to_weight.get(key)
        f.check(
            got == expected,
            f"{label}: PreSync helm weight for {key!r} must be {expected!r} "
            f"(effective Argo sync-wave); got {got!r}",
        )

    def _as_int(key: str) -> int | None:
        raw = role_to_weight.get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    sa, img, app = (
        _as_int("serviceaccount"),
        _as_int("imagepull"),
        _as_int("application-secrets"),
    )
    db, notif = (
        _as_int("db-migration"),
        _as_int("notifications-db-migration"),
    )
    if None not in (sa, img, app, db, notif):
        f.check(
            sa < img < app < db < notif,  # type: ignore[operator]
            f"{label}: PreSync wave order must be "
            f"SA({sa}) < imagepull({img}) < secrets({app}) < "
            f"db({db}) < notifications({notif})",
        )

    backends = find_docs(docs, "Deployment", "tranzr-service")
    normalizers = find_docs(docs, "Deployment", "worker-vision-normalizer")
    if backends:
        api_ann = doc_annotations(backends[0])
        api_wave = api_ann.get(ARGO_SYNC_WAVE)
        f.check(
            api_wave == "0",
            f"{label}: API ordinary Sync {ARGO_SYNC_WAVE} must remain 0 "
            f"(not a hook; got {api_wave!r})",
        )
        f.check(
            ARGO_HOOK_ANNOTATION not in api_ann,
            f"{label}: API must not carry {ARGO_HOOK_ANNOTATION}",
        )
        f.check(
            HELM_HOOK_ANNOTATION not in api_ann,
            f"{label}: API must not be a Helm hook (Sync wave 0)",
        )
    if normalizers:
        n_wave = doc_annotations(normalizers[0]).get(ARGO_SYNC_WAVE)
        f.check(
            n_wave == "1",
            f"{label}: VisionNormalizer ordinary Sync {ARGO_SYNC_WAVE} must "
            f"remain 1 (got {n_wave!r})",
        )


def assert_no_normalization_contract(
    f: Failures, label: str, docs: list[dict[str, Any]]
) -> None:
    normalizers = find_docs(docs, "Deployment", "worker-vision-normalizer")
    f.check(
        not normalizers,
        f"{label}: must not render a VisionNormalizer Deployment",
    )
    assert_no_vision_mode_activation_job(f, label, docs)
    for role, name_substr in (
        ("API", "tranzr-service"),
        ("processor", "worker-processor"),
        ("scheduler", "worker-scheduler"),
    ):
        matches = find_docs(docs, "Deployment", name_substr)
        if not matches:
            continue
        got = normalization_env(container_env(matches[0]))
        f.check(
            not got,
            f"{label}: {role} must not receive Vision__Normalization__* env (got {got!r})",
        )


def assert_no_vision_mode_activation_job(
    f: Failures, label: str, docs: list[dict[str, Any]]
) -> None:
    jobs = find_docs(docs, "Job", ACTIVATION_JOB_NAME_SUBSTR)
    f.check(
        not jobs,
        f"{label}: VisionModeActuator Job must be absent (got "
        f"{[(((j.get('metadata') or {}).get('name'))) for j in jobs]!r})",
    )
    for doc in docs:
        if doc.get("kind") != "Job":
            continue
        env = container_env(doc)
        f.check(
            env.get("Worker__Role") != "VisionModeActuator",
            f"{label}: no Job may set Worker__Role=VisionModeActuator "
            f"(job={(doc.get('metadata') or {}).get('name')!r})",
        )


def assert_no_actuator_transition_startup_fields(
    f: Failures, label: str, docs: list[dict[str, Any]]
) -> None:
    """Actuator/transition startup fields must be absent on all workloads."""
    for doc in docs:
        kind = str(doc.get("kind") or "")
        if kind not in ("Deployment", "Job", "CronJob"):
            continue
        name = str((doc.get("metadata") or {}).get("name") or "")
        env = container_env(doc)
        for key in FORBIDDEN_ACTUATOR_ENV:
            f.check(
                key not in env,
                f"{label}: {kind}/{name} must not set retired actuator field {key}",
            )
        f.check(
            env.get("Worker__Role") != "VisionModeActuator",
            f"{label}: {kind}/{name} must not use Worker__Role=VisionModeActuator",
        )


def assert_retired_actuator_and_ordinary_presync(
    f: Failures, label: str, docs: list[dict[str, Any]]
) -> None:
    """No VisionModeActuator Job; ordinary migrators + Sync waves remain.

    Effective Argo PreSync for SA/secrets/migrators uses Helm→Argo mapping when
    no explicit Argo hook exists anywhere in the render (docs/user-guide/helm.md).
    """
    mapping_ok = assert_helm_only_hook_mapping_active(f, label, docs)
    assert_no_vision_mode_activation_job(f, label, docs)
    assert_no_actuator_transition_startup_fields(f, label, docs)

    # values.yaml must not retain activation-only job configuration.
    jobs_values = (_DEFAULT_VALUES.get("jobs") or {})
    f.check(
        "visionModeActivation" not in jobs_values,
        f"{label}: values.yaml must not define jobs.visionModeActivation",
    )
    norm = (
        ((_DEFAULT_VALUES.get("deployments") or {}).get("workerVisionNormalizer") or {}).get(
            "normalization"
        )
        or {}
    )
    for retired_key in ("apiTransitionFromMode", "apiTransitionOperationId"):
        f.check(
            retired_key not in norm,
            f"{label}: values normalization must not define {retired_key}",
        )

    # History/enum vocabulary retained; LegacySync is not an active-intake support claim.
    f.check(
        set(DURABLE_INTAKE_MODE_NAMES) == {"LegacySync", "IntakePaused", "AsyncQueue"},
        f"{label}: intake mode history vocabulary must retain "
        f"LegacySync/IntakePaused/AsyncQueue",
    )

    db_jobs = find_docs(docs, "Job", "db-migration")
    notif_jobs = find_docs(docs, "Job", "notifications-db-migration")
    f.check(len(db_jobs) >= 1, f"{label}: ordinary db-migration Job must render")
    f.check(
        len(notif_jobs) >= 1,
        f"{label}: ordinary notifications-db-migration Job must render",
    )
    if db_jobs:
        db_ann = doc_annotations(db_jobs[0])
        f.check(
            db_ann.get(HELM_HOOK_WEIGHT) == "0",
            f"{label}: db-migration helm hook-weight must remain 0",
        )
        f.check(
            set(_split_csv_annotation(db_ann.get(HELM_HOOK_ANNOTATION)))
            == {"pre-install", "pre-upgrade"},
            f"{label}: db-migration must keep Helm pre-install,pre-upgrade hooks",
        )
    if notif_jobs:
        n_ann = doc_annotations(notif_jobs[0])
        f.check(
            n_ann.get(HELM_HOOK_WEIGHT) == "1",
            f"{label}: notifications-db-migration helm hook-weight must remain 1",
        )
        f.check(
            set(_split_csv_annotation(n_ann.get(HELM_HOOK_ANNOTATION)))
            == {"pre-install", "pre-upgrade"},
            f"{label}: notifications-db-migration must keep Helm "
            f"pre-install,pre-upgrade hooks",
        )

    if mapping_ok:
        assert_presync_weight_ordering(f, label, docs)


def assert_async_normalization_contract(
    f: Failures, label: str, docs: list[dict[str, Any]]
) -> None:
    normalizers = find_docs(docs, "Deployment", "worker-vision-normalizer")
    f.check(
        len(normalizers) == 1,
        f"{label}: expected exactly one VisionNormalizer Deployment",
    )
    if not normalizers:
        return

    normalizer = normalizers[0]
    spec = normalizer.get("spec") or {}
    pod = pod_spec(normalizer)
    container = first_container(normalizer)
    env = container_env(normalizer)

    f.check(spec.get("replicas") == 1, f"{label}: VisionNormalizer replicas must equal 1")
    f.check(
        ((normalizer.get("metadata") or {}).get("annotations") or {}).get(
            "argocd.argoproj.io/sync-wave"
        )
        == "1",
        f"{label}: VisionNormalizer must start in Argo sync wave 1",
    )
    processors = find_docs(docs, "Deployment", "worker-processor")
    if processors:
        f.check(
            container_image(normalizer) == container_image(processors[0]),
            f"{label}: VisionNormalizer must use the movesWorker image",
        )
        f.check(
            container_image(normalizer)
            == f"ghcr.io/tranz-r/tranzr-moves-worker:{SHARED_MOVES_VERSION}",
            f"{label}: VisionNormalizer image must be {SHARED_MOVES_VERSION}",
        )
    f.check(
        env.get("Worker__Role") == "VisionNormalizer",
        f"{label}: VisionNormalizer worker role",
    )

    expected = {
        "Vision__Normalization__IntakeMode": "AsyncQueue",
        "Vision__Normalization__MessagingEnabled": "true",
        "Vision__Normalization__IncludeConsumer": "true",
        "Vision__Normalization__ConsumerReady": "true",
        "Vision__Normalization__QueueName": NORMALIZATION_QUEUE,
        "Vision__Normalization__SpoolDirectory": NORMALIZATION_SPOOL_DIRECTORY,
        "Vision__Normalization__SpoolQuotaBytes": NORMALIZATION_SPOOL_QUOTA_BYTES,
        "Vision__Normalization__SpoolFreeSpaceHeadroomBytes": NORMALIZATION_SPOOL_HEADROOM_BYTES,
    }
    for key, value in expected.items():
        f.check(env.get(key) == value, f"{label}: normalizer {key} == {value!r}")
    f.check(
        env.get(MEDIA_NORMALIZATION_POLICY_KEY) == NATIVE_NORMALIZATION_POLICY_VERSION,
        f"{label}: normalizer {MEDIA_NORMALIZATION_POLICY_KEY} == "
        f"{NATIVE_NORMALIZATION_POLICY_VERSION!r}",
    )
    f.check(
        env.get(NORMALIZATION_POLICY_KEY) == NATIVE_NORMALIZATION_POLICY_VERSION,
        f"{label}: normalizer {NORMALIZATION_POLICY_KEY} == "
        f"{NATIVE_NORMALIZATION_POLICY_VERSION!r}",
    )
    f.check(
        "Vision__Normalization__TransitionFromMode" not in env,
        f"{label}: normalizer must not own the mode-transition source",
    )
    f.check(
        "Vision__Normalization__TransitionOperationId" not in env,
        f"{label}: normalizer must not own the mode-transition operation ID",
    )

    secret_env = {
        key: secret_ref_key(value)
        for key, value in env.items()
        if isinstance(value, dict) and secret_ref_key(value) is not None
    }
    db_secret = secret_env.get("ConnectionStrings__TranzrMovesDatabaseConnection")
    allowed_db_secrets = {
        "tranzr-supabase-database-connection-string",
        "tranzr-supabase-transaction-database-connection-string",
    }
    f.check(
        db_secret in allowed_db_secrets,
        f"{label}: normalizer DB secret must be session or transaction pooler key "
        f"(got {db_secret!r})",
    )
    f.check(
        secret_env.get("RABBITMQ_PASSWORD") == "platform-rabbitmq-password",
        f"{label}: normalizer RabbitMQ password secret key",
    )
    f.check(
        secret_env.get("AZURE_STORAGE_CONNECTION_STRING") == AZURE_STORAGE_SECRET_KEY,
        f"{label}: normalizer Azure storage secret key",
    )
    f.check(
        set(secret_env) == {
            "ConnectionStrings__TranzrMovesDatabaseConnection",
            "RABBITMQ_PASSWORD",
            "AZURE_STORAGE_CONNECTION_STRING",
        },
        f"{label}: normalizer secret scope mismatch (got {secret_env!r})",
    )
    for forbidden in ("STRIPE_API_KEY", "REDIS_PASSWORD", "OPENROUTER_API_KEY"):
        f.check(forbidden not in env, f"{label}: normalizer must not receive {forbidden}")

    command_blob = "\n".join(
        str(item) for item in ((container.get("command") or []) + (container.get("args") or []))
    )
    f.check(
        "ConnectionStrings__rabbitmq" in command_blob and "RABBITMQ_PASSWORD" in command_blob,
        f"{label}: normalizer must construct the RabbitMQ connection string from platform config",
    )
    f.check(
        "ConnectionStrings__redis" not in command_blob,
        f"{label}: normalizer must not configure Redis",
    )

    resources = container.get("resources") or {}
    limits = resources.get("limits") or {}
    f.check(limits.get("memory") == "1Gi", f"{label}: normalizer memory limit must equal 1Gi")

    mounts = {m.get("name"): m for m in (container.get("volumeMounts") or [])}
    volumes = {v.get("name"): v for v in (pod.get("volumes") or [])}
    spool_mount = mounts.get("vision-normalization-spool") or {}
    spool_volume = volumes.get("vision-normalization-spool") or {}
    f.check(
        spool_mount.get("mountPath") == NORMALIZATION_SPOOL_DIRECTORY,
        f"{label}: normalizer spool volume mount path",
    )
    f.check(
        (spool_volume.get("emptyDir") or {}).get("sizeLimit") == "2560Mi",
        f"{label}: normalizer emptyDir spool sizeLimit must equal quota plus headroom (2560Mi)",
    )

    for probe_name in ("livenessProbe", "readinessProbe", "startupProbe"):
        probe = container.get(probe_name) or {}
        command = (probe.get("exec") or {}).get("command") or []
        f.check(bool(command), f"{label}: normalizer {probe_name} must use an exec process probe")
        f.check(
            "httpGet" not in probe and "tcpSocket" not in probe,
            f"{label}: normalizer {probe_name} must not use an HTTP/TCP probe",
        )
        f.check(
            "kill -0 1" in " ".join(str(part) for part in command),
            f"{label}: normalizer {probe_name} must check the worker process",
        )

    backend = find_docs(docs, "Deployment", "tranzr-service")
    if backend:
        be = container_env(backend[0])
        api_expected = {
            "Vision__Normalization__IntakeMode": "AsyncQueue",
            "Vision__Normalization__MessagingEnabled": "true",
            "Vision__Normalization__IncludeConsumer": "false",
            "Vision__Normalization__ConsumerReady": "true",
            "Vision__Normalization__QueueName": NORMALIZATION_QUEUE,
        }
        for key, value in api_expected.items():
            f.check(be.get(key) == value, f"{label}: API {key} == {value!r}")
        f.check(
            be.get(MEDIA_NORMALIZATION_POLICY_KEY) == NATIVE_NORMALIZATION_POLICY_VERSION,
            f"{label}: API {MEDIA_NORMALIZATION_POLICY_KEY} == "
            f"{NATIVE_NORMALIZATION_POLICY_VERSION!r}",
        )
        f.check(
            be.get(NORMALIZATION_POLICY_KEY) == NATIVE_NORMALIZATION_POLICY_VERSION,
            f"{label}: API {NORMALIZATION_POLICY_KEY} == "
            f"{NATIVE_NORMALIZATION_POLICY_VERSION!r}",
        )
        f.check(
            "Vision__Normalization__TransitionFromMode" not in be,
            f"{label}: stable async API must not own a transition source",
        )
        f.check(
            "Vision__Normalization__TransitionOperationId" not in be,
            f"{label}: stable async API must not own a transition operation ID",
        )
        f.check(
            (
                ((backend[0].get("metadata") or {}).get("annotations") or {}).get(
                    "argocd.argoproj.io/sync-wave"
                )
            )
            == "0",
            f"{label}: API transition owner must complete in Argo sync wave 0",
        )
        for key in (
            "Vision__Normalization__SpoolDirectory",
            "Vision__Normalization__SpoolQuotaBytes",
            "Vision__Normalization__SpoolFreeSpaceHeadroomBytes",
        ):
            f.check(key not in be, f"{label}: API must not receive worker-only {key}")

    processor = find_docs(docs, "Deployment", "worker-processor")
    scheduler = find_docs(docs, "Deployment", "worker-scheduler")
    if processor:
        pe = container_env(processor[0])
        consumer_norm = {
            key: value
            for key, value in normalization_env(pe).items()
            if key != NORMALIZATION_POLICY_KEY
        }
        f.check(
            not consumer_norm,
            f"{label}: existing processor must not become a normalization consumer "
            f"(got {consumer_norm!r})",
        )
        f.check(
            pe.get(MEDIA_NORMALIZATION_POLICY_KEY) == NATIVE_NORMALIZATION_POLICY_VERSION,
            f"{label}: processor {MEDIA_NORMALIZATION_POLICY_KEY} == "
            f"{NATIVE_NORMALIZATION_POLICY_VERSION!r}",
        )
        f.check(
            pe.get(NORMALIZATION_POLICY_KEY) == NATIVE_NORMALIZATION_POLICY_VERSION,
            f"{label}: processor {NORMALIZATION_POLICY_KEY} == "
            f"{NATIVE_NORMALIZATION_POLICY_VERSION!r}",
        )
    if scheduler:
        se = container_env(scheduler[0])
        f.check(
            not normalization_env(se),
            f"{label}: existing scheduler must not become a normalization consumer "
            f"(got {normalization_env(se)!r})",
        )
        f.check(
            se.get(MEDIA_NORMALIZATION_POLICY_KEY) == NATIVE_NORMALIZATION_POLICY_VERSION,
            f"{label}: scheduler {MEDIA_NORMALIZATION_POLICY_KEY} == "
            f"{NATIVE_NORMALIZATION_POLICY_VERSION!r}",
        )

    # Fleet identity: API / processor / scheduler media policy must match the worker.
    worker_media = env.get(MEDIA_NORMALIZATION_POLICY_KEY)
    for role, doc_list, key in (
        ("API", backend, MEDIA_NORMALIZATION_POLICY_KEY),
        ("API", backend, NORMALIZATION_POLICY_KEY),
        ("processor", processor, MEDIA_NORMALIZATION_POLICY_KEY),
        ("processor", processor, NORMALIZATION_POLICY_KEY),
        ("scheduler", scheduler, MEDIA_NORMALIZATION_POLICY_KEY),
    ):
        if not doc_list:
            continue
        got = container_env(doc_list[0]).get(key)
        f.check(
            got == worker_media == NATIVE_NORMALIZATION_POLICY_VERSION,
            f"{label}: {role} {key} must agree with normalizer native policy "
            f"(role={got!r} worker={worker_media!r})",
        )


def secret_ref_key(env_val: Any) -> str | None:
    if isinstance(env_val, dict):
        return env_val.get("key")
    return None


def assert_catalogue_learning_contract(
    f: Failures, label: str, be: dict[str, Any], pe: dict[str, Any], se: dict[str, Any]
) -> None:
    for env_map, role in ((be, "API"), (pe, "processor"), (se, "scheduler")):
        for key, expected in CATALOGUE_EXPECTED.items():
            f.check(
                env_map.get(key) == expected,
                f"{label}: {role} {key} == {expected!r} (got {env_map.get(key)!r})",
            )


def assert_confirmation_activation_contract(
    f: Failures,
    label: str,
    be: dict[str, Any],
    pe: dict[str, Any],
    se: dict[str, Any],
    *,
    api_expected: str,
) -> None:
    f.check(
        be.get(CONFIRMATION_ACTIVATION_KEY) == api_expected,
        f"{label}: API {CONFIRMATION_ACTIVATION_KEY} == {api_expected!r} "
        f"(got {be.get(CONFIRMATION_ACTIVATION_KEY)!r})",
    )
    f.check(
        CONFIRMATION_ACTIVATION_KEY not in pe,
        f"{label}: processor must not receive {CONFIRMATION_ACTIVATION_KEY}",
    )
    f.check(
        CONFIRMATION_ACTIVATION_KEY not in se,
        f"{label}: scheduler must not receive {CONFIRMATION_ACTIVATION_KEY}",
    )


def assert_active_vision(f: Failures, label: str, docs: list[dict[str, Any]]) -> None:
    backend = find_docs(docs, "Deployment", "tranzr-service")
    processor = find_docs(docs, "Deployment", "worker-processor")
    scheduler = find_docs(docs, "Deployment", "worker-scheduler")
    f.check(len(backend) == 1, f"{label}: expected one backend Deployment")
    f.check(len(processor) == 1, f"{label}: expected one processor Deployment")
    f.check(len(scheduler) == 1, f"{label}: expected one scheduler Deployment")
    if not (backend and processor and scheduler):
        return

    be = container_env(backend[0])
    pe = container_env(processor[0])
    se = container_env(scheduler[0])

    f.check(be.get("Vision__Analysis__MessagingEnabled") == "true", f"{label}: API messaging on")
    f.check(be.get("Vision__Analysis__IncludeConsumer") == "false", f"{label}: API includeConsumer false")
    f.check(be.get("Vision__Analysis__HubEnabled") == "false", f"{label}: API hubEnabled false")
    f.check(be.get("Vision__Provider") == "Fake", f"{label}: API provider Fake")
    f.check(be.get("Vision__Analysis__QueueName") == "vision-analysis", f"{label}: API queue name")
    f.check(be.get("Vision__Media__Container") == "quote-media-vision", f"{label}: API media container")

    f.check(pe.get("Vision__Analysis__MessagingEnabled") == "true", f"{label}: processor messaging on")
    f.check(pe.get("Vision__Provider") == "OpenRouter", f"{label}: processor provider OpenRouter")
    f.check(se.get("Vision__Media__RetentionWorkerEnabled") == "true", f"{label}: retention on")

    assert_catalogue_learning_contract(f, label, be, pe, se)
    assert_confirmation_activation_contract(f, label, be, pe, se, api_expected="true")


def allowed_content_types_from_env(env: dict[str, Any]) -> list[str]:
    """Collect Vision__Media__AllowedContentTypes__N literals in index order."""
    indexed: list[tuple[int, str]] = []
    for key, value in env.items():
        if not key.startswith(ALLOWED_CONTENT_TYPES_ENV_PREFIX):
            continue
        suffix = key[len(ALLOWED_CONTENT_TYPES_ENV_PREFIX) :]
        if not suffix.isdigit() or not isinstance(value, str):
            continue
        indexed.append((int(suffix), value))
    indexed.sort(key=lambda item: item[0])
    return [value for _, value in indexed]


def assert_allowed_content_types_contract(
    f: Failures,
    *,
    labels_docs: list[tuple[str, list[dict[str, Any]]]],
) -> None:
    """API pins the five-MIME native allowlist; processor/scheduler omit env (app defaults)."""
    for label, docs in labels_docs:
        for role, name_substr in (
            ("API", "tranzr-service"),
            ("processor", "worker-processor"),
            ("scheduler", "worker-scheduler"),
        ):
            matches = find_docs(docs, "Deployment", name_substr)
            f.check(len(matches) == 1, f"{label}: {role} present for AllowedContentTypes")
            if not matches:
                continue
            env = container_env(matches[0])
            got = allowed_content_types_from_env(env)
            stray = [
                key
                for key in env
                if key.startswith(ALLOWED_CONTENT_TYPES_ENV_PREFIX)
                and (
                    not key[len(ALLOWED_CONTENT_TYPES_ENV_PREFIX) :].isdigit()
                    or not isinstance(env.get(key), str)
                )
            ]
            f.check(not stray, f"{label}: {role} stray AllowedContentTypes keys {stray!r}")

            if role == "API":
                f.check(
                    got == list(API_ALLOWED_CONTENT_TYPES),
                    f"{label}: API AllowedContentTypes must be exactly "
                    f"{list(API_ALLOWED_CONTENT_TYPES)!r} (got {got!r})",
                )
            else:
                f.check(
                    not got,
                    f"{label}: {role} must not emit AllowedContentTypes env "
                    f"(app defaults / non-API; got {got!r})",
                )


def assert_staging_production_vision_contract(
    f: Failures, staging_docs: list[dict[str, Any]], production_docs: list[dict[str, Any]]
) -> None:
    """Staging and production keep full Vision__* env parity (shared photo defaults)."""
    for role, name_substr in (
        ("API", "tranzr-service"),
        ("processor", "worker-processor"),
        ("scheduler", "worker-scheduler"),
    ):
        stg = find_docs(staging_docs, "Deployment", name_substr)
        prod = find_docs(production_docs, "Deployment", name_substr)
        f.check(len(stg) == 1 and len(prod) == 1, f"contract: {role} Deployments present")
        if not (stg and prod):
            continue
        stg_contract = vision_env_contract(stg[0])
        prod_contract = vision_env_contract(prod[0])
        f.check(
            stg_contract == prod_contract,
            f"contract: staging vs production {role} Vision__* env mismatch "
            f"(staging={stg_contract!r} production={prod_contract!r})",
        )


def assert_normalization_policy_identity(
    f: Failures,
    *,
    labels_docs: list[tuple[str, list[dict[str, Any]]]],
) -> None:
    """Shared/default, staging, and production pin native v3 with VisionNormalizer enabled."""
    for label, docs in labels_docs:
        scheduler = find_docs(docs, "Deployment", "worker-scheduler")
        f.check(len(scheduler) == 1, f"{label}: scheduler present for policy identity")
        if not scheduler:
            continue
        got = container_env(scheduler[0]).get(MEDIA_NORMALIZATION_POLICY_KEY)
        f.check(
            got == NATIVE_NORMALIZATION_POLICY_VERSION,
            f"{label}: scheduler {MEDIA_NORMALIZATION_POLICY_KEY} == "
            f"{NATIVE_NORMALIZATION_POLICY_VERSION!r} (got {got!r})",
        )
        f.check(
            len(find_docs(docs, "Deployment", "worker-vision-normalizer")) == 1,
            f"{label}: VisionNormalizer must be enabled under native async policy",
        )


def assert_api_secrets(f: Failures, label: str, docs: list[dict[str, Any]]) -> None:
    backend = find_docs(docs, "Deployment", "tranzr-service")[0]
    be = container_env(backend)
    f.check(
        secret_ref_key(be.get("ConnectionStrings__TranzrMovesDatabaseConnection")) is not None,
        f"{label}: API has DB secret ref",
    )
    f.check("RABBITMQ_PASSWORD" in be, f"{label}: API has RabbitMQ password env")
    f.check(
        secret_ref_key(be.get("AZURE_STORAGE_CONNECTION_STRING")) == AZURE_STORAGE_SECRET_KEY,
        f"{label}: API Azure storage secret key",
    )
    f.check("OPENROUTER_API_KEY" not in be, f"{label}: API must not receive OPENROUTER_API_KEY")
    f.check(be.get("Vision__Analysis__IncludeConsumer") == "false", f"{label}: includeConsumer false")
    f.check(be.get("Vision__Analysis__HubEnabled") == "false", f"{label}: hubEnabled false")


def assert_processor(f: Failures, label: str, docs: list[dict[str, Any]], *, activated: bool) -> None:
    processor = find_docs(docs, "Deployment", "worker-processor")[0]
    pe = container_env(processor)
    f.check(
        secret_ref_key(pe.get("ConnectionStrings__TranzrMovesDatabaseConnection")) is not None,
        f"{label}: processor has DB secret ref",
    )
    f.check("RABBITMQ_PASSWORD" in pe, f"{label}: processor has RabbitMQ password env")
    f.check(
        secret_ref_key(pe.get("AZURE_STORAGE_CONNECTION_STRING")) == AZURE_STORAGE_SECRET_KEY,
        f"{label}: processor Azure storage secret key",
    )
    f.check(
        secret_ref_key(pe.get("OPENROUTER_API_KEY")) == OPENROUTER_SECRET_KEY,
        f"{label}: processor OpenRouter secret key ref",
    )
    for k, expected in OPENROUTER_BOUNDS.items():
        f.check(pe.get(k) == expected, f"{label}: processor {k} == {expected!r} (got {pe.get(k)!r})")

    if activated:
        f.check(pe.get("Vision__Analysis__MessagingEnabled") == "true", f"{label}: processor messaging on")
        f.check(pe.get("Vision__Provider") == "OpenRouter", f"{label}: processor provider OpenRouter")
    else:
        f.check(pe.get("Vision__Analysis__MessagingEnabled") == "false", f"{label}: processor messaging off")
        f.check(pe.get("Vision__Provider") == "Fake", f"{label}: processor provider Fake")


def assert_scheduler(f: Failures, label: str, docs: list[dict[str, Any]], *, retention: bool) -> None:
    scheduler = find_docs(docs, "Deployment", "worker-scheduler")[0]
    se = container_env(scheduler)
    f.check(
        secret_ref_key(se.get("ConnectionStrings__TranzrMovesDatabaseConnection")) is not None,
        f"{label}: scheduler has DB secret ref",
    )
    f.check("RABBITMQ_PASSWORD" in se, f"{label}: scheduler has RabbitMQ password env")
    f.check(
        secret_ref_key(se.get("AZURE_STORAGE_CONNECTION_STRING")) == AZURE_STORAGE_SECRET_KEY,
        f"{label}: scheduler Azure storage secret key",
    )
    f.check("OPENROUTER_API_KEY" not in se, f"{label}: scheduler must not receive OPENROUTER_API_KEY")
    expected = "true" if retention else "false"
    f.check(
        se.get("Vision__Media__RetentionWorkerEnabled") == expected,
        f"{label}: retentionWorkerEnabled == {expected}",
    )
    f.check(se.get("Vision__Media__Container") == "quote-media-vision", f"{label}: scheduler media container")
    f.check(se.get("Vision__Media__PolicyVersion") == "media-policy-v1", f"{label}: media policy version")
    f.check(
        se.get("Vision__Media__RetentionPolicyVersion") == "retention-policy-v1",
        f"{label}: retention policy version",
    )
    f.check(
        se.get(MEDIA_NORMALIZATION_POLICY_KEY) == NATIVE_NORMALIZATION_POLICY_VERSION,
        f"{label}: normalization policy version == {NATIVE_NORMALIZATION_POLICY_VERSION!r}",
    )
    f.check(se.get("Vision__Media__IntentTtlMinutes") == "10", f"{label}: intent TTL")
    f.check(se.get("Vision__Media__UnverifiedIntentGraceMinutes") == "120", f"{label}: unverified grace")
    f.check(se.get("Vision__Media__SweeperIntervalMinutes") == "15", f"{label}: sweeper interval")
    f.check(se.get("Vision__Media__SweeperBatchSize") == "100", f"{label}: sweeper batch")


def assert_no_openrouter_on_others(f: Failures, label: str, docs: list[dict[str, Any]]) -> None:
    for kind, name in (
        ("Deployment", "tranzr-gateway"),
        ("Deployment", "notifications"),
        ("Deployment", "tranzr-service"),
        ("Deployment", "worker-scheduler"),
    ):
        matches = find_docs(docs, kind, name)
        for doc in matches:
            env = container_env(doc)
            f.check("OPENROUTER_API_KEY" not in env, f"{label}: {name} must not have OPENROUTER_API_KEY")
            for k, v in env.items():
                if isinstance(v, str) and re.search(r"(?i)sk-or-|openrouter\.ai/api/v1/keys", v):
                    f.check(False, f"{label}: {name} env {k} looks like an OpenRouter credential")


def assert_openrouter_externalsecret(f: Failures, label: str, docs: list[dict[str, Any]]) -> None:
    secrets = [d for d in docs if d.get("kind") == "ExternalSecret"]
    app_secret = None
    for d in secrets:
        if (d.get("metadata") or {}).get("name") == "tranzrmoves-secrets":
            app_secret = d
            break
    f.check(app_secret is not None, f"{label}: tranzrmoves-secrets ExternalSecret present")
    if not app_secret:
        return
    data = ((app_secret.get("spec") or {}).get("data")) or []
    match = None
    for item in data:
        if item.get("secretKey") == OPENROUTER_SECRET_KEY:
            match = item
            break
    f.check(match is not None, f"{label}: ExternalSecret maps {OPENROUTER_SECRET_KEY}")
    if match:
        remote = ((match.get("remoteRef") or {}).get("key"))
        f.check(remote == OPENROUTER_SECRET_KEY, f"{label}: remoteRef.key == {OPENROUTER_SECRET_KEY}")
    blob = yaml.dump(app_secret)
    f.check("sk-or-" not in blob, f"{label}: ExternalSecret must not embed literal OpenRouter secrets")
    f.check(re.search(r"(?i)api[_-]?key\s*[:=]\s*['\"]?[a-zA-Z0-9]{20,}", blob) is None,
            f"{label}: ExternalSecret must not embed literal API key values")


def assert_prod_images(f: Failures, docs: list[dict[str, Any]]) -> None:
    expected = {
        "tranzr-service": f"ghcr.io/tranz-r/tranzr-moves-services:{SHARED_MOVES_VERSION}",
        "worker-processor": f"ghcr.io/tranz-r/tranzr-moves-worker:{SHARED_MOVES_VERSION}",
        "worker-scheduler": f"ghcr.io/tranz-r/tranzr-moves-worker:{SHARED_MOVES_VERSION}",
        "db-migration": f"ghcr.io/tranz-r/tranzr-moves-db-migrator:{SHARED_MOVES_VERSION}",
    }
    for name_substr, image in expected.items():
        kind = "Job" if name_substr == "db-migration" else "Deployment"
        matches = find_docs(docs, kind, name_substr)
        f.check(len(matches) >= 1, f"production: missing {kind} containing {name_substr}")
        if matches:
            got = container_image(matches[0])
            f.check(got == image, f"production: {name_substr} image {got!r} != {image!r}")


def assert_migration_enabled(f: Failures, docs: list[dict[str, Any]]) -> None:
    jobs = find_docs(docs, "Job", "db-migration")
    f.check(len(jobs) >= 1, "production: db-migration Job must be rendered (enabled)")
    if jobs:
        f.check(
            container_image(jobs[0]) == f"ghcr.io/tranz-r/tranzr-moves-db-migrator:{SHARED_MOVES_VERSION}",
            f"production: migrator image must be {SHARED_MOVES_VERSION}",
        )


def write_override(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def main() -> int:
    failures = Failures()
    print("== Vision V1 GitOps verifier ==")
    print(f"chart: {CHART}")

    try:
        helm_lint([VALUES_DEFAULT])
        print("PASS helm lint (default)")
        helm_lint([VALUES_DEFAULT, VALUES_STAGING])
        print("PASS helm lint (staging)")
        helm_lint([VALUES_DEFAULT, VALUES_PRODUCTION])
        print("PASS helm lint (production)")
    except RuntimeError as exc:
        failures.append(str(exc))

    try:
        default_render = helm_template("trm-default", [VALUES_DEFAULT])
        staging_render = helm_template("trm-stg", [VALUES_DEFAULT, VALUES_STAGING])
        production_render = helm_template("trm-prod", [VALUES_DEFAULT, VALUES_PRODUCTION])
        print("PASS helm template (default, staging, production)")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    default_docs = load_docs(default_render)
    staging_docs = load_docs(staging_render)
    production_docs = load_docs(production_render)

    common_labels_docs = (
        ("default", default_docs),
        ("staging", staging_docs),
        ("production", production_docs),
    )
    for label, docs in common_labels_docs:
        backend = find_docs(docs, "Deployment", "tranzr-service")
        if backend:
            img = container_image(backend[0])
            failures.check(
                img == f"ghcr.io/tranz-r/tranzr-moves-services:{SHARED_MOVES_VERSION}",
                f"{label}: backend image {img!r} != shared release {SHARED_MOVES_VERSION!r}",
            )
    print(f"PASS shared moves image {SHARED_MOVES_VERSION} (default + staging + production)")

    # Shared baseline activates Vision in default, staging, and production.
    for label, docs in common_labels_docs:
        assert_active_vision(failures, label, docs)
    print("PASS active Vision gates (default + staging + production)")
    print("PASS API Fake / includeConsumer false / hub false (all three)")
    print(
        "PASS catalogue-learning contract "
        "(categoryAware=true; candidate/admin/manufacturer=false) "
        "on API/processor/scheduler (default + staging + production)"
    )
    print(
        "PASS confirmation-review activation true on API only "
        "(absent from processor/scheduler) (default + staging + production)"
    )

    assert_staging_production_vision_contract(failures, staging_docs, production_docs)
    print("PASS staging/production Vision__* env contract identical")

    assert_allowed_content_types_contract(failures, labels_docs=list(common_labels_docs))
    print(
        "PASS AllowedContentTypes "
        f"(API exact {list(API_ALLOWED_CONTENT_TYPES)} on default/staging/production; "
        "non-API omit env → app defaults)"
    )

    assert_normalization_policy_identity(failures, labels_docs=list(common_labels_docs))
    print(
        "PASS normalization policy identity "
        f"(default/staging/production={NATIVE_NORMALIZATION_POLICY_VERSION})"
    )

    for label, docs in common_labels_docs:
        assert_async_normalization_contract(failures, label, docs)
    print(
        "PASS shared VisionNormalizer AsyncQueue workload, isolation, spool, probes, "
        "native policy keys, and API producer contract (default + staging + production)"
    )

    for label, docs in common_labels_docs:
        assert_retired_actuator_and_ordinary_presync(failures, label, docs)
    print(
        "PASS VisionModeActuator retired (no Job / no transition startup fields); "
        "ordinary migrators PreSync 0/1 + Sync API 0 / VisionNormalizer 1; "
        f"durable modes {list(DURABLE_INTAKE_MODE_NAMES)}; pin {SHARED_MOVES_VERSION}"
    )

    assert_prod_images(failures, production_docs)
    assert_migration_enabled(failures, production_docs)
    print(f"PASS production images + migrator == {SHARED_MOVES_VERSION}")

    for label, docs in (
        ("default", default_docs),
        ("staging", staging_docs),
        ("production", production_docs),
    ):
        assert_api_secrets(failures, label, docs)
        assert_processor(failures, label, docs, activated=True)
        assert_scheduler(failures, label, docs, retention=True)
        assert_openrouter_externalsecret(failures, label, docs)
        assert_no_openrouter_on_others(failures, label, docs)
    print("PASS API DB/RabbitMQ/Azure; no OpenRouter; hub/includeConsumer false (all)")
    print("PASS processor active + OpenRouter bounds + secret refs (all)")
    print("PASS scheduler retention on + retention settings + Azure storage (all)")
    print("PASS OpenRouter ExternalSecret mapping (no literal secret)")
    print("PASS gateway/API/scheduler/notifications lack OpenRouter credentials")

    with tempfile.TemporaryDirectory(prefix="vision-v1-verify-") as tmp:
        tmp_path = Path(tmp)
        rollback_override = tmp_path / "rollback-dark.yaml"
        write_override(
            rollback_override,
            {
                "features": {
                    "vision": {
                        "analysis": {
                            "apiMessagingEnabled": False,
                            "processorMessagingEnabled": False,
                        },
                        "provider": {"processor": "Fake"},
                        "media": {"retentionWorkerEnabled": False},
                        "confirmationReview": {"activationEnabled": False},
                    }
                }
            },
        )

        try:
            # Override shared baseline (production stack inherits active defaults).
            rollback_render = helm_template(
                "trm-prod-rollback",
                [VALUES_DEFAULT, VALUES_PRODUCTION],
                extra_values_file=rollback_override,
            )
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1

        rollback_docs = load_docs(rollback_render)
        be = container_env(find_docs(rollback_docs, "Deployment", "tranzr-service")[0])
        pe = container_env(find_docs(rollback_docs, "Deployment", "worker-processor")[0])
        se = container_env(find_docs(rollback_docs, "Deployment", "worker-scheduler")[0])

        failures.check(be.get("Vision__Analysis__MessagingEnabled") == "false", "rollback: API messaging off")
        failures.check(be.get("Vision__Provider") == "Fake", "rollback: API provider stays Fake")
        failures.check(be.get("Vision__Analysis__IncludeConsumer") == "false", "rollback: includeConsumer false")
        failures.check(be.get("Vision__Analysis__HubEnabled") == "false", "rollback: hubEnabled false")
        assert_processor(failures, "rollback", rollback_docs, activated=False)
        assert_scheduler(failures, "rollback", rollback_docs, retention=False)
        assert_catalogue_learning_contract(failures, "rollback", be, pe, se)
        assert_confirmation_activation_contract(
            failures, "rollback", be, pe, se, api_expected="false"
        )
        # Messaging rollback must not darken the shared photo runtime contract.
        rollback_backend = find_docs(rollback_docs, "Deployment", "tranzr-service")[0]
        failures.check(
            container_image(rollback_backend)
            == f"ghcr.io/tranz-r/tranzr-moves-services:{SHARED_MOVES_VERSION}",
            "rollback: shared moves image must remain pinned",
        )
        assert_async_normalization_contract(failures, "rollback", rollback_docs)
        assert_retired_actuator_and_ordinary_presync(
            failures, "rollback", rollback_docs
        )
        assert_allowed_content_types_contract(
            failures, labels_docs=[("rollback", rollback_docs)]
        )
        assert_normalization_policy_identity(
            failures, labels_docs=[("rollback", rollback_docs)]
        )
        print("PASS rollback/dark override returns messaging/provider/retention to off/Fake")
        print(
            "PASS rollback keeps common photo runtime "
            f"({SHARED_MOVES_VERSION} / native AsyncQueue / five API MIME)"
        )
        print(
            "PASS rollback keeps VisionModeActuator absent "
            "(messaging dark ≠ mode rewrite / no auto-downgrade)"
        )
        print(
            "PASS catalogue-learning contract unchanged under messaging/provider/retention rollback "
            "(categoryAware=true; candidate/admin/manufacturer=false)"
        )
        print(
            "PASS confirmation-review activation false on API under rollback "
            "(still absent from processor/scheduler)"
        )

        assert_no_openrouter_on_others(failures, "rollback", rollback_docs)
        assert_openrouter_externalsecret(failures, "rollback", rollback_docs)

        # Mutation self-test: deliberately wrong concurrency must fail OPENROUTER_BOUNDS checks.
        wrong_concurrency_override = tmp_path / "wrong-concurrency.yaml"
        write_override(
            wrong_concurrency_override,
            {
                "features": {
                    "vision": {
                        "openRouter": {
                            "maxConcurrentRequests": 99,
                        }
                    }
                }
            },
        )
        try:
            wrong_render = helm_template(
                "trm-mut-concurrency",
                [VALUES_DEFAULT],
                extra_values_file=wrong_concurrency_override,
            )
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        mut_failures = Failures()
        assert_processor(mut_failures, "mutation-concurrency", load_docs(wrong_render), activated=True)
        concurrency_rejected = any(
            "Vision__OpenRouter__MaxConcurrentRequests" in msg and "== '4'" in msg
            for msg in mut_failures
        )
        failures.check(
            concurrency_rejected,
            "mutation self-test: wrong MaxConcurrentRequests must be rejected by OPENROUTER_BOUNDS",
        )
        print("PASS mutation self-test rejects wrong MaxConcurrentRequests")

        # Mutation self-test: flipping category-aware off must fail CATALOGUE_EXPECTED.
        wrong_catalogue_override = tmp_path / "wrong-catalogue.yaml"
        write_override(
            wrong_catalogue_override,
            {
                "features": {
                    "vision": {
                        "catalogueLearning": {
                            "categoryAwareInferenceEnabled": False,
                        }
                    }
                }
            },
        )
        try:
            wrong_catalogue_render = helm_template(
                "trm-mut-catalogue",
                [VALUES_DEFAULT],
                extra_values_file=wrong_catalogue_override,
            )
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        cat_docs = load_docs(wrong_catalogue_render)
        cat_be = container_env(find_docs(cat_docs, "Deployment", "tranzr-service")[0])
        cat_pe = container_env(find_docs(cat_docs, "Deployment", "worker-processor")[0])
        cat_se = container_env(find_docs(cat_docs, "Deployment", "worker-scheduler")[0])
        cat_mut_failures = Failures()
        assert_catalogue_learning_contract(
            cat_mut_failures, "mutation-catalogue", cat_be, cat_pe, cat_se
        )
        catalogue_rejected = any(
            "Vision__CatalogueLearning__CategoryAwareInferenceEnabled" in msg
            and "== 'true'" in msg
            for msg in cat_mut_failures
        )
        failures.check(
            catalogue_rejected,
            "mutation self-test: categoryAwareInferenceEnabled=false must be rejected by CATALOGUE_EXPECTED",
        )
        print("PASS mutation self-test rejects categoryAwareInferenceEnabled=false")

        # Mutation self-test: confirmation activation false on API must fail contract.
        wrong_confirmation_override = tmp_path / "wrong-confirmation.yaml"
        write_override(
            wrong_confirmation_override,
            {
                "features": {
                    "vision": {
                        "confirmationReview": {
                            "activationEnabled": False,
                        }
                    }
                }
            },
        )
        try:
            wrong_confirmation_render = helm_template(
                "trm-mut-confirmation",
                [VALUES_DEFAULT],
                extra_values_file=wrong_confirmation_override,
            )
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        conf_docs = load_docs(wrong_confirmation_render)
        conf_be = container_env(find_docs(conf_docs, "Deployment", "tranzr-service")[0])
        conf_pe = container_env(find_docs(conf_docs, "Deployment", "worker-processor")[0])
        conf_se = container_env(find_docs(conf_docs, "Deployment", "worker-scheduler")[0])
        conf_mut_failures = Failures()
        assert_confirmation_activation_contract(
            conf_mut_failures,
            "mutation-confirmation",
            conf_be,
            conf_pe,
            conf_se,
            api_expected="true",
        )
        confirmation_rejected = any(
            CONFIRMATION_ACTIVATION_KEY in msg and "== 'true'" in msg
            for msg in conf_mut_failures
        )
        failures.check(
            confirmation_rejected,
            "mutation self-test: confirmation activationEnabled=false must be rejected "
            "by confirmation activation contract",
        )
        print("PASS mutation self-test rejects confirmation activationEnabled=false")

        # Mutation self-test: missing confirmation activation key on API must fail contract.
        missing_conf_docs = copy.deepcopy(default_docs)
        missing_api_doc = find_docs(missing_conf_docs, "Deployment", "tranzr-service")[0]
        missing_containers = (
            ((missing_api_doc.get("spec") or {}).get("template") or {}).get("spec") or {}
        ).get("containers") or []
        failures.check(
            bool(missing_containers),
            "mutation self-test: missing-key fixture must retain API containers",
        )
        if missing_containers:
            missing_containers[0]["env"] = [
                item
                for item in (missing_containers[0].get("env") or [])
                if item.get("name") != CONFIRMATION_ACTIVATION_KEY
            ]
        miss_be = container_env(missing_api_doc)
        miss_pe = container_env(
            find_docs(missing_conf_docs, "Deployment", "worker-processor")[0]
        )
        miss_se = container_env(
            find_docs(missing_conf_docs, "Deployment", "worker-scheduler")[0]
        )
        failures.check(
            CONFIRMATION_ACTIVATION_KEY not in miss_be,
            "mutation self-test: fixture must omit API confirmation activation key",
        )
        miss_mut_failures = Failures()
        assert_confirmation_activation_contract(
            miss_mut_failures,
            "mutation-confirmation-missing",
            miss_be,
            miss_pe,
            miss_se,
            api_expected="true",
        )
        confirmation_missing_rejected = any(
            CONFIRMATION_ACTIVATION_KEY in msg and "== 'true'" in msg
            for msg in miss_mut_failures
        )
        failures.check(
            confirmation_missing_rejected,
            "mutation self-test: missing confirmation activation key must be rejected "
            "by confirmation activation contract",
        )
        print("PASS mutation self-test rejects missing confirmation activation key")

        # Mutation self-test: worker policy drift vs features media policy fails closed.
        drift_override = tmp_path / "policy-drift.yaml"
        write_override(
            drift_override,
            {
                "deployments": {
                    "workerVisionNormalizer": {
                        "normalization": {
                            "policyVersion": LEGACY_NORMALIZATION_POLICY_VERSION,
                        }
                    }
                }
            },
        )
        try:
            drift_render = helm_template(
                "trm-mut-policy-drift",
                [VALUES_DEFAULT],
                extra_values_file=drift_override,
            )
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        drift_failures = Failures()
        assert_async_normalization_contract(
            drift_failures, "mutation-policy-drift", load_docs(drift_render)
        )
        policy_drift_rejected = any(
            "must agree with normalizer native policy" in msg
            or (
                "normalizer" in msg
                and MEDIA_NORMALIZATION_POLICY_KEY in msg
                and NATIVE_NORMALIZATION_POLICY_VERSION in msg
            )
            for msg in drift_failures
        )
        failures.check(
            policy_drift_rejected,
            "mutation self-test: worker policyVersion drift from native v3 must fail closed",
        )
        print("PASS mutation self-test rejects native policy drift")

        # Normalizer off → no dedicated Deployment and no actuator Job.
        # Fleet media NormalizationPolicyVersion env may still pin on API/workers.
        normalizer_off = tmp_path / "normalizer-off.yaml"
        write_override(
            normalizer_off,
            {"deployments": {"workerVisionNormalizer": {"enabled": False}}},
        )
        try:
            off_render = helm_template(
                "trm-norm-off",
                [VALUES_DEFAULT],
                extra_values_file=normalizer_off,
            )
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        off_docs = load_docs(off_render)
        failures.check(
            not find_docs(off_docs, "Deployment", "worker-vision-normalizer"),
            "normalizer-off: must not render VisionNormalizer Deployment",
        )
        assert_no_vision_mode_activation_job(failures, "normalizer-off", off_docs)
        assert_no_actuator_transition_startup_fields(
            failures, "normalizer-off", off_docs
        )
        print("PASS normalizer disabled excludes VisionNormalizer + VisionModeActuator")

        # IntakePaused operational pause remains a valid intakeMode (no actuator Job).
        paused_intake = tmp_path / "paused-intake.yaml"
        write_override(
            paused_intake,
            {
                "deployments": {
                    "workerVisionNormalizer": {
                        "normalization": {"intakeMode": "IntakePaused"},
                    }
                }
            },
        )
        try:
            paused_render = helm_template(
                "trm-paused-intake",
                [VALUES_DEFAULT],
                extra_values_file=paused_intake,
            )
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        paused_docs = load_docs(paused_render)
        assert_no_vision_mode_activation_job(failures, "intake-paused", paused_docs)
        paused_api = find_docs(paused_docs, "Deployment", "tranzr-service")
        failures.check(len(paused_api) == 1, "intake-paused: API Deployment present")
        if paused_api:
            failures.check(
                container_env(paused_api[0]).get("Vision__Normalization__IntakeMode")
                == "IntakePaused",
                "intake-paused: API IntakeMode must remain operational IntakePaused",
            )
        print(
            "PASS IntakePaused operational pause retained without VisionModeActuator Job"
        )

        # LegacySync remains a persisted enum / history identity only — not a supported
        # active intake configuration. No positive override/render-support assertion:
        # current path is AsyncQueue; operational pause remains IntakePaused (checked above).

        # RED mutation: inject retired VisionModeActuator Job → absence contract fails.
        injected_docs = copy.deepcopy(default_docs)
        injected_docs.append(
            {
                "kind": "Job",
                "metadata": {
                    "name": f"trm-default-{ACTIVATION_JOB_NAME_SUBSTR}",
                    "annotations": {
                        HELM_HOOK_ANNOTATION: "pre-install,pre-upgrade",
                        HELM_HOOK_WEIGHT: "2",
                        HELM_HOOK_DELETE_POLICY: "before-hook-creation,hook-succeeded",
                    },
                },
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "vision-mode-actuator",
                                    "image": (
                                        "ghcr.io/tranz-r/tranzr-moves-worker:"
                                        f"{SHARED_MOVES_VERSION}"
                                    ),
                                    "env": [
                                        {
                                            "name": "Worker__Role",
                                            "value": "VisionModeActuator",
                                        },
                                        {
                                            "name": (
                                                "Vision__Normalization__"
                                                "DeploymentIdentity"
                                            ),
                                            "value": "moves-injected-activation-v1",
                                        },
                                    ],
                                }
                            ]
                        }
                    }
                },
            }
        )
        inject_failures = Failures()
        assert_retired_actuator_and_ordinary_presync(
            inject_failures, "mutation-actuator-injected", injected_docs
        )
        inject_rejected = any(
            "VisionModeActuator" in msg or ACTIVATION_JOB_NAME_SUBSTR in msg
            for msg in inject_failures
        )
        failures.check(
            inject_rejected,
            "mutation self-test: injected VisionModeActuator Job must fail closed",
        )
        print("PASS mutation self-test rejects injected VisionModeActuator Job")

        # RED mutation: inject transition startup env on API → fail closed.
        transition_docs = copy.deepcopy(default_docs)
        api_docs = find_docs(transition_docs, "Deployment", "tranzr-service")
        failures.check(
            len(api_docs) == 1,
            "mutation self-test: default render must include API for transition inject",
        )
        if api_docs:
            containers = (
                ((api_docs[0].get("spec") or {}).get("template") or {}).get("spec") or {}
            ).get("containers") or []
            if containers:
                env_list = containers[0].setdefault("env", [])
                env_list.append(
                    {
                        "name": "Vision__Normalization__TransitionFromMode",
                        "value": "LegacySync",
                    }
                )
                env_list.append(
                    {
                        "name": "Vision__Normalization__TransitionOperationId",
                        "value": "op-injected",
                    }
                )
        transition_failures = Failures()
        assert_no_actuator_transition_startup_fields(
            transition_failures, "mutation-transition-fields", transition_docs
        )
        transition_rejected = any(
            "TransitionFromMode" in msg or "TransitionOperationId" in msg
            for msg in transition_failures
        )
        failures.check(
            transition_rejected,
            "mutation self-test: injected transition startup fields must fail closed",
        )
        print("PASS mutation self-test rejects injected transition startup fields")

        # RED mutation: mixed Argo+Helm on migrator → mapping disabled / contract fails.
        mixed_docs = copy.deepcopy(default_docs)
        mixed_jobs = find_docs(mixed_docs, "Job", "db-migration")
        failures.check(
            len(mixed_jobs) >= 1,
            "mutation self-test: default render must include db-migration fixture",
        )
        if mixed_jobs:
            ann = (mixed_jobs[0].setdefault("metadata", {})).setdefault("annotations", {})
            ann[ARGO_HOOK_ANNOTATION] = "PreSync"
            ann[ARGO_SYNC_WAVE] = "0"
            ann[ARGO_HOOK_DELETE_POLICY] = "BeforeHookCreation,HookSucceeded"
        mixed_failures = Failures()
        assert_retired_actuator_and_ordinary_presync(
            mixed_failures, "mutation-mixed-hooks", mixed_docs
        )
        mixed_rejected = any(
            "mixed Argo+Helm" in msg
            or "BLOCKER" in msg
            or ARGO_HOOK_ANNOTATION in msg
            for msg in mixed_failures
        )
        failures.check(
            mixed_rejected,
            "mutation self-test: mixed Argo+Helm hooks must fail closed",
        )
        print("PASS mutation self-test rejects mixed Argo+Helm migrator hooks")

        # RED mutation: migrator helm weight drift → fail closed.
        drift_docs = copy.deepcopy(default_docs)
        drift_jobs = find_docs(drift_docs, "Job", "db-migration")
        if drift_jobs:
            ann = (drift_jobs[0].setdefault("metadata", {})).setdefault("annotations", {})
            ann[HELM_HOOK_WEIGHT] = "2"
            ann.pop(ARGO_HOOK_ANNOTATION, None)
            ann.pop(ARGO_SYNC_WAVE, None)
            ann.pop(ARGO_HOOK_DELETE_POLICY, None)
        drift_failures = Failures()
        assert_retired_actuator_and_ordinary_presync(
            drift_failures, "mutation-migrator-wave-drift", drift_docs
        )
        wave_rejected = any(
            HELM_HOOK_WEIGHT in msg
            or "PreSync helm weight" in msg
            or "wave order" in msg
            or "db-migration" in msg
            for msg in drift_failures
        )
        failures.check(
            wave_rejected,
            "mutation self-test: migrator helm weight drift must fail closed",
        )
        print("PASS mutation self-test rejects migrator effective wave drift")

    activation_template = CHART / "templates" / "jobs" / "vision-mode-activation.yaml"
    failures.check(
        not activation_template.exists(),
        f"source must not retain retired template {activation_template}",
    )

    for path in (
        VALUES_DEFAULT,
        VALUES_PRODUCTION,
        VALUES_STAGING,
        CHART / "templates" / "_helpers.tpl",
        CHART / "templates" / "deployments" / "backend-deployment.yaml",
        CHART / "templates" / "deployments" / "worker-processor-deployment.yaml",
        CHART / "templates" / "deployments" / "worker-scheduler-deployment.yaml",
        CHART / "templates" / "deployments" / "worker-vision-normalizer-deployment.yaml",
        CHART / "templates" / "jobs" / "db-migration.yaml",
        CHART / "templates" / "jobs" / "notifications-db-migration.yaml",
    ):
        if not path.exists():
            failures.check(False, f"source missing: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        failures.check("sk-or-" not in text, f"source {path.name}: must not contain sk-or- literal")
        failures.check(
            "AccountKey=" not in text,
            f"source {path.name}: must not embed Azure AccountKey literals",
        )
        failures.check(
            "VisionModeActuator" not in text,
            f"source {path.name}: must not reference VisionModeActuator",
        )
        failures.check(
            "visionModeActivation" not in text,
            f"source {path.name}: must not reference visionModeActivation",
        )
        failures.check(
            "apiTransitionFromMode" not in text
            and "apiTransitionOperationId" not in text,
            f"source {path.name}: must not retain transition startup value keys",
        )

    if failures:
        print("\nFAILED assertions:")
        for msg in failures:
            print(f"  - {msg}")
        return 1

    print("\nAll Vision V1 GitOps assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
