"""Grant or revoke an application role on a sign-in account.

Run by the Solution Support Owner on the server, never exposed over HTTP:

    uv run python -m scripts.grant_role --email someone@wpi.edu \\
        --role study_personnel --authorized-by "Named person, ticket/approval ref"

    uv run python -m scripts.grant_role --email someone@wpi.edu --role instructor --revoke \\
        --authorized-by "..."

Roles: instructor, study_personnel, export_approver, support_owner.
Only the IRB's approved study personnel get study_personnel. Every change is
written to the audit log. Once sign-in moves to Entra ID, these come from
Entra app-role assignments instead and this script goes away.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import asyncpg

from app.config import settings
from app.observability.audit import audit, configure_audit_logging

ROLES = ("instructor", "study_personnel", "export_approver", "support_owner")


async def main(email: str, role: str, revoke: bool, authorized_by: str) -> int:
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        user = await conn.fetchrow(
            "SELECT id, participant_id FROM identity.users WHERE email = $1",
            email.strip().lower(),
        )
        if user is None:
            print("No account with that address. It must sign in once first.", file=sys.stderr)
            return 1
        if revoke:
            tag = await conn.execute(
                "DELETE FROM identity.account_roles WHERE user_id = $1 AND role = $2",
                user["id"], role,
            )
            changed = not tag.endswith(" 0")
        else:
            tag = await conn.execute(
                """
                INSERT INTO identity.account_roles (user_id, role, granted_by)
                VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
                """,
                user["id"], role, authorized_by,
            )
            changed = not tag.endswith(" 0")
    finally:
        await conn.close()

    audit(
        "admin.role_revoked" if revoke else "admin.role_granted",
        "success" if changed else "failure",
        actor_user_id=user["id"],
        role=role,
        authorized_by=authorized_by,
        changed=changed,
        via="grant_role script",
    )
    print(f"{'revoked' if revoke else 'granted'} {role}" if changed else "no change")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--revoke", action="store_true")
    parser.add_argument(
        "--authorized-by", required=True,
        help="Who authorized this change (name + approval/ticket reference). Recorded.",
    )
    args = parser.parse_args()
    configure_audit_logging()
    sys.exit(asyncio.run(main(args.email, args.role, args.revoke, args.authorized_by)))
