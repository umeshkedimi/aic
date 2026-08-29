"""Mints short-lived, scoped K8s ServiceAccount credentials from the
operator's own admin kubeconfig (design doc §1.11).

Shared by both privilege-separated code paths in this codebase:
`aic_agents.tools.k8s.load_investigator_credentials` (read-only,
`aic-investigator`, T7) and `aic_agents.execution.load_executor_credentials`
(write-scoped, `aic-executor`, T10). Factored out here rather than
duplicated so the one piece of subprocess/kubeconfig-parsing logic that
both credential loaders share has one implementation — the two loaders
differ only in which ServiceAccount name they mint a token for, never in
how minting works.

The operator's admin kubeconfig is used exactly once, at process startup,
to mint a token — every subsequent K8s call (investigative read or
executor write) uses only the minted, RBAC-scoped token returned here,
never the admin credential itself.
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_SUBPROCESS_TIMEOUT_SECONDS = 10.0


@dataclass(slots=True)
class ServiceAccountCredentials:
    server: str
    ca_cert_path: Path
    token: str
    namespace: str


def mint_service_account_credentials(
    *,
    context: str,
    namespace: str,
    service_account: str,
    token_duration: str = "1h",
    kubectl: str = "kubectl",
) -> ServiceAccountCredentials:
    """Mint `service_account`'s own credentials from the operator's
    kubeconfig (a one-time, admin-privileged bootstrap step — that's how
    any credential is provisioned)."""
    server = subprocess.run(
        [
            kubectl,
            "config",
            "view",
            "--raw",
            "-o",
            f'jsonpath={{.clusters[?(@.name=="{context}")].cluster.server}}',
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()
    ca_data_b64 = subprocess.run(
        [
            kubectl,
            "config",
            "view",
            "--raw",
            "-o",
            f'jsonpath={{.clusters[?(@.name=="{context}")].cluster.certificate-authority-data}}',
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()
    token = subprocess.run(
        [
            kubectl,
            "create",
            "token",
            service_account,
            "-n",
            namespace,
            f"--duration={token_duration}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()

    fd, ca_file_name = tempfile.mkstemp(suffix=".crt")
    os.close(fd)
    ca_file = Path(ca_file_name)
    ca_file.write_bytes(base64.b64decode(ca_data_b64))
    return ServiceAccountCredentials(
        server=server, ca_cert_path=ca_file, token=token, namespace=namespace
    )
