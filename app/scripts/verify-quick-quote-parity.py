#!/usr/bin/env python3
"""Read-only Helm checks for quote-preview engine and marketing-origin alignment."""
from pathlib import Path
import subprocess
import re

CHART = Path(__file__).resolve().parents[1]


def main() -> None:
    for environment, domain in (("staging", "tranzrmoves.com"), ("production", "tranzzer.com")):
        rendered = subprocess.run(
            ["helm", "template", "quick-quote-verify", str(CHART),
             "-f", str(CHART / "values.yaml"), "-f", str(CHART / f"values-{environment}.yaml")],
            check=True, capture_output=True, text=True,
        ).stdout
        deployments = [doc for doc in rendered.split("\n---") if re.search(r"^kind: Deployment$", doc, re.M)]
        gateway = next(doc for doc in deployments if "app.kubernetes.io/component: gateway" in doc)
        env_pattern = r"- name: ([A-Za-z0-9_]+)\n\s+value: \"([^\"]*)\""
        origins = {value for name, value in re.findall(env_pattern, gateway) if name.startswith("Cors__AllowedOrigins__")}
        assert {f"https://{domain}", f"https://www.{domain}"} <= origins, f"{environment}: marketing CORS origins missing"
        backend = next(doc for doc in deployments if re.search(r"^  name: tranzr-service$", doc, re.M))
        bindings = re.findall(env_pattern, backend)
        names = [name for name, _ in bindings if name.startswith("ItemTransport__")]
        assert len(names) == len(set(names)), f"{environment}: duplicate item transport environment settings"
        env = dict(bindings)
        assert env.get("ItemTransport__RoutePlanningMode") == "Enabled", f"{environment}: unified routing is not enabled"
        assert 1 <= int(env.get("ItemTransport__RouteReservationMinutes", "0")) <= 60, f"{environment}: invalid reservation lifetime"
        assert env.get("ItemTransport__Enabled") == "true", f"{environment}: item engine unexpectedly disabled"
        assert env.get("ItemTransport__Vroom__Enabled") == "true", f"{environment}: item preview requires solver"
        assert env.get("ItemTransport__Vroom__BaseUrl") == "http://tranzr-vroom/", f"{environment}: solver target mismatch"
        jobs = [doc for doc in rendered.split("\n---") if re.search(r"^kind: Job$", doc, re.M)]
        migration = next(doc for doc in jobs if "app.kubernetes.io/component: db-migration" in doc)
        assert "helm.sh/hook: pre-install,pre-upgrade" in migration, f"{environment}: migration must precede deployment"
        api_image = re.search(r'image: "([^\"]+)"', backend).group(1)
        migration_image = re.search(r'image: "([^\"]+)"', migration).group(1)
        assert api_image.rsplit(":", 1)[-1] == migration_image.rsplit(":", 1)[-1], f"{environment}: backend/migration version mismatch"
        print(f"PASS {environment}: unified routing Enabled, reservation lifetime, marketing origins, solver and versioned migration")


if __name__ == "__main__":
    main()
