"""PostgreSQL database backend that authenticates to AWS Aurora with IAM.

Aurora IAM auth uses a short-lived (15 minute) token in place of a password.
The token only needs to be valid at connection time, so we generate a fresh one
in ``get_connection_params`` — which Django calls every time it opens a new
connection. Pair this with a ``CONN_MAX_AGE`` under 15 minutes so connections are
recycled (and re-tokenised) within the token's lifetime.

Set ``ENGINE = "streak.db.aurora_iam"`` and leave ``PASSWORD`` empty. AWS
credentials are resolved by boto3 in the usual order (instance/task IAM role in
production, or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY locally).
"""

import os
import threading

import boto3
from django.conf import settings
from django.db.backends.postgresql import base as postgresql_base

_client_lock = threading.Lock()
_rds_clients: dict[str, object] = {}


def _rds_client(region: str):
    client = _rds_clients.get(region)
    if client is None:
        with _client_lock:
            client = _rds_clients.get(region)
            if client is None:
                client = boto3.client("rds", region_name=region)
                _rds_clients[region] = client
    return client


def _aws_region() -> str:
    return (
        getattr(settings, "AWS_REGION", None)
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "eu-north-1"
    )


class DatabaseWrapper(postgresql_base.DatabaseWrapper):
    def get_connection_params(self):
        params = super().get_connection_params()
        host = params.get("host")
        user = params.get("user")
        port = params.get("port") or 5432
        if host and user:
            region = _aws_region()
            params["password"] = _rds_client(region).generate_db_auth_token(
                DBHostname=host,
                Port=int(port),
                DBUsername=user,
                Region=region,
            )
        # IAM authentication requires TLS to the database.
        params.setdefault("sslmode", "require")
        return params
