#!/usr/bin/env python3
import importlib.util
import json
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).with_name("verify_staging_rollout.py")
spec = importlib.util.spec_from_file_location("verify_staging_rollout", MODULE_PATH)
assert spec is not None
assert spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def argo_resource(manifest):
    """Match the Argo CD application resource API response shape."""
    return {"manifest": json.dumps(manifest)}


class VerifyStagingRolloutTests(unittest.TestCase):
    def test_accepts_synced_healthy_expected_revision(self):
        application = {
            "status": {
                "sync": {"status": "Synced", "revision": "abc123"},
                "health": {"status": "Healthy"},
                "resources": [
                    {
                        "group": "apps",
                        "kind": "Deployment",
                        "namespace": "tranzr-moves-staging",
                        "name": "tranzr-moves-worker-vision-normalizer",
                        "status": "Synced",
                        "health": {"status": "Healthy"},
                    }
                ],
            }
        }

        self.assertEqual(module.application_errors(application, "abc123"), [])

    def test_rejects_stale_or_unhealthy_application(self):
        application = {
            "status": {
                "sync": {"status": "OutOfSync", "revision": "old"},
                "health": {"status": "Degraded"},
                "resources": [],
            }
        }

        errors = module.application_errors(application, "abc123")

        self.assertIn("application revision is 'old', expected 'abc123'", errors)
        self.assertIn("application sync is 'OutOfSync', expected 'Synced'", errors)
        self.assertIn("application health is 'Degraded', expected 'Healthy'", errors)
        self.assertIn("normalizer Deployment is absent from application resources", errors)

    def test_accepts_live_normalizer_contract_from_argo_manifest(self):
        resource = argo_resource(
            {
                "spec": {
                    "replicas": 1,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "worker",
                                    "image": "ghcr.io/tranz-r/tranzr-moves-worker:0.122.0",
                                    "env": [
                                        {"name": "Worker__Role", "value": "VisionNormalizer"},
                                        {"name": "Vision__Normalization__IntakeMode", "value": "LegacySync"},
                                    ],
                                    "resources": {"limits": {"memory": "1Gi"}},
                                }
                            ]
                        }
                    },
                },
                "status": {"readyReplicas": 1, "updatedReplicas": 1},
            }
        )

        self.assertEqual(
            module.normalizer_errors(
                resource, "ghcr.io/tranz-r/tranzr-moves-worker:0.122.0"
            ),
            [],
        )

    def test_reads_staging_moves_version(self):
        values = '\nnamespaceOverride: "staging"\nimages:\n  movesVersion: "0.122.0"\nglobal:\n  enabled: true\n'

        self.assertEqual(module.staging_moves_version(values), "0.122.0")

    def test_rejects_wrong_image_role_mode_or_readiness(self):
        resource = argo_resource(
            {
                "spec": {
                    "replicas": 1,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "worker",
                                    "image": "ghcr.io/tranz-r/tranzr-moves-worker:0.121.5",
                                    "env": [
                                        {"name": "Worker__Role", "value": "Processor"},
                                        {"name": "Vision__Normalization__IntakeMode", "value": "AsyncQueue"},
                                    ],
                                    "resources": {"limits": {"memory": "768Mi"}},
                                }
                            ]
                        }
                    },
                },
                "status": {"readyReplicas": 0, "updatedReplicas": 1},
            }
        )

        errors = module.normalizer_errors(
            resource, "ghcr.io/tranz-r/tranzr-moves-worker:0.122.0"
        )

        self.assertEqual(len(errors), 5)


if __name__ == "__main__":
    unittest.main()
