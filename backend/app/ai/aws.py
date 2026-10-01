"""AWS credentials and clients — resolved one way, for every Bedrock call.

SR-2026-052 item 1.6: least-privilege, short-lived, server-only. Credentials
come from botocore's standard chain — on the WPI VM that is an IAM Roles
Anywhere `credential_process` profile or vault-issued STS credentials (Nutanix
has no instance metadata service); in development, an SSO / assumed-role
profile. botocore refreshes them before they expire. No access key is ever
read from SES configuration, and none is ever sent to the browser.

The boto3 clients (Titan embeddings, Guardrails) use the chain directly. The
bidirectional-streaming SDK used for Nova Sonic is a separate code base, so
`BotocoreIdentityResolver` hands it the same refreshed credentials instead of
letting it resolve its own.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

from app.config import settings


@lru_cache(maxsize=1)
def session() -> boto3.Session:
    return boto3.Session(region_name=settings.aws_region)


@lru_cache(maxsize=1)
def bedrock_runtime() -> Any:
    """boto3 `bedrock-runtime` client (InvokeModel, ApplyGuardrail)."""
    return session().client(
        "bedrock-runtime",
        config=Config(retries={"max_attempts": 4, "mode": "adaptive"}, read_timeout=60),
    )


class BotocoreIdentityResolver:
    """Smithy identity resolver backed by botocore's refreshing credentials.

    Implements the `IdentityResolver` protocol of the AWS bidirectional SDK:
    `get_identity(properties=...)` returns fresh `AWSCredentialsIdentity`.
    """

    async def get_identity(self, *, properties: Any) -> Any:
        from aws_sdk_bedrock_runtime.config import AWSCredentialsIdentity

        creds = session().get_credentials()
        if creds is None:
            raise RuntimeError(
                "No AWS credentials available from the standard chain "
                "(role / STS / Roles Anywhere / vault)."
            )
        frozen = creds.get_frozen_credentials()
        return AWSCredentialsIdentity(
            access_key_id=frozen.access_key,
            secret_access_key=frozen.secret_key,
            session_token=frozen.token,
        )

    def invalidate(self) -> None:
        # botocore owns refresh; nothing cached here.
        return None
