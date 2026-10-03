#!/usr/bin/env bash
# Restore an SES backup into a NEW database (SR-2026-052 item 3.2, SEC-BCK-001).
#
#   sudo -u postgres GNUPGHOME=<keyring holding the backup private key> \
#       ./ses-restore.sh BACKUP_FILE NEW_DATABASE [OWNER]
#
# BACKUP_FILE   ses-<db>-<UTC timestamp>.dump.gpg, written by app/jobs/backup.py
# NEW_DATABASE  created here; must not exist. This script never restores over
#               an existing database, the live one included.
# OWNER         role that owns the new database (default: sis)
#
# Run as the postgres OS user: the dump recreates the pgvector extension and
# the original ownership and grants, which needs a superuser. The roles the
# dump refers to must exist (on a rebuilt server, create them first as in
# deploy/WPI_DEPLOY.md). The plaintext dump is streamed from gpg straight into
# pg_restore and never written to disk.
#
# Restoring is only the first step. Before the application uses the restored
# copy, replay what happened since the backup (app/jobs/after_restore.py) and
# run the retention job; see deploy/WPI_DEPLOY.md, "Restoring a backup".
set -euo pipefail

die() { echo "restore: $1" >&2; exit "${2:-1}"; }

[[ $# -ge 2 && $# -le 3 ]] || die "usage: $0 BACKUP_FILE NEW_DATABASE [OWNER]" 64
backup=$1
target=$2
owner=${3:-sis}

[[ $target =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || die "NEW_DATABASE must be lower-case letters, digits and _" 64
[[ $owner =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || die "OWNER must be lower-case letters, digits and _" 64
[[ -f $backup ]] || die "$backup not found" 66
name=$(basename "$backup")
[[ $name =~ ^ses-[A-Za-z0-9_]+-([0-9]{8}T[0-9]{6}Z)\.dump\.gpg$ ]] \
    || die "$name is not a backup file name this job writes" 65
taken=${BASH_REMATCH[1]}

# 1. Integrity: the checksum written when the backup was taken.
if [[ -f $backup.sha256 ]]; then
    (cd "$(dirname "$backup")" && sha256sum --check --quiet "$name.sha256") \
        || die "checksum mismatch: do not use this backup" 65
elif [[ ${SES_RESTORE_WITHOUT_CHECKSUM:-} == 1 ]]; then
    echo "restore: WARNING: no checksum file; continuing because SES_RESTORE_WITHOUT_CHECKSUM=1" >&2
else
    die "no $name.sha256 beside the backup (set SES_RESTORE_WITHOUT_CHECKSUM=1 to go ahead anyway)" 65
fi

# 2. Never over an existing database.
exists=$(psql -X -At -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT 1 FROM pg_database WHERE datname = '$target'")
[[ -z $exists ]] || die "database $target already exists; choose a new name" 73

# 3. Decrypt and restore, streamed. Any failure drops the half-made database.
# The key's passphrase is asked for here and handed to gpg over a pipe: gpg's
# own prompt cannot reach the operator's terminal from under sudo, and the
# passphrase never goes into a file, an argument or the environment.
passphrase=
if [[ -t 0 ]]; then
    read -rsp "Passphrase for the backup key (Enter if it has none): " passphrase
    echo >&2
fi
createdb --owner="$owner" "$target"
if ! gpg --batch --quiet --pinentry-mode loopback --passphrase-fd 3 --decrypt "$backup" \
        3< <(printf '%s' "$passphrase") | pg_restore --exit-on-error --dbname="$target"; then
    unset passphrase
    dropdb --if-exists "$target"
    die "restore failed; $target was dropped" 1
fi
unset passphrase

# 4. What came back.
count() { psql -X -At -d "$target" -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM $1"; }
revision=$(psql -X -At -d "$target" -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version")
cat <<EOF
restored:        $target (owner $owner)
backup taken:    $taken
schema revision: $revision
interview_sessions:     $(count interview_sessions)
session_evaluations:    $(count session_evaluations)
session_flags:          $(count session_flags)
participants:           $(count participants)
identity.users:         $(count identity.users)
research.session_records: $(count research.session_records)

Next (deploy/WPI_DEPLOY.md, "Restoring a backup"):
  1. replay since the backup:  python -m app.jobs.after_restore --since $taken --audit-log <file>
  2. run the retention job against $target
  3. only then point the application at it
EOF
