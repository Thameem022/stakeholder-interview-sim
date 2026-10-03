# SES Incident Runbook

Covers SR-2026-052 SEC-INC-001 and the IRB-27-0033 adverse-event path. Named
people for each role are in the solution document; this runbook refers to
roles only.

| Role | In this runbook |
|---|---|
| **Support Owner** | Receives every flag alert, reviews flags, purges sessions, contacts the others below. |
| **Study PIs** (Business & Data Owners) | Decide on and file IRB adverse-event reports; told about every Scenario A and B incident. |
| **Information Security** | Told about data exposure, AI-provider incidents, and anything involving credentials. |
| **Student Affairs** | Told immediately about any student in distress. |
| **SOC** | Receives security events from the SIEM; the route for cloud-provider incidents. |
| **IRB** | Receives adverse-event reports from the Study PIs. |

> **Safety first.** If anything in a session suggests a student may be at
> immediate risk, contact campus emergency services *before* doing anything
> else in this runbook.

---

## How incidents reach you

- **A session flag.** Students can flag their own session, instructors can flag
  any session, and Bedrock Guardrails flag automatically (a persona reply that trips the guardrail also ends the interview — in a written interview the reply is withheld and never stored; harmful feedback is withheld). Every
  flag emits the audit event `incident.session_flagged` with `severity: high`.
  The SIEM alert rule on that event pages the Support Owner. The event names
  the session, reason and source but never the note or any transcript text.
- **A SIEM alert** on other security events (auth failure spikes, export
  refusals, rate-limit storms).
- **A notice from the cloud AI provider** (outage, security bulletin).
- **A direct report** from a student, instructor or study PI. Ask them to flag the
  session in SES too, so there is a record.

Flag reasons: `sensitive_disclosure`, `distress`, `harmful_ai_output`, `other`.

---

## Reviewing a flag

1. Sign in to SES with the account that holds the `support_owner` role.
2. List open flags. There is no admin screen yet, so use the browser console on
   the SES site:
   ```js
   await (await fetch('/api/admin/flags')).json()
   ```
   Each flag shows its session id, source, reason, the reporter's note and when
   it was raised.
3. Decide which scenario below applies. Mark the flag reviewed once you have
   acted:
   ```js
   const csrf = document.cookie.match(/(?:^|; )(?:__Host-)?sis_csrf=([^;]+)/)[1];
   await fetch(`/api/admin/flags/${FLAG_ID}/review`, {method: 'POST', headers: {'X-CSRF-Token': csrf}})
   ```

Do not copy transcript text into tickets, email or chat. Refer to the session
id and the flag id.

---

## Scenario A — a student disclosed real sensitive information or is in distress

1. **Distress:** contact Student Affairs **immediately** with the session id
   and the fact of the flag. Student Affairs resolves the student's identity
   through the Support Owner, who is the only person who can read the
   identity store. Do not forward the transcript.
2. **Sensitive disclosure** (real personal, family, health or financial
   details about the student or someone else): **purge the session** (below)
   unless the Study PIs need it preserved for the IRB report. If so, keep it no
   longer than that report requires, then purge.
3. Tell the **Study PIs**. They decide whether this is a reportable adverse
   event and file it with the **IRB** within the IRB's required timeframe.
   Record the IRB reference on the flag's ticket.
4. If the student had consented to research, the purge also deletes the
   research copy. Tell the Study PIs that the participant's record changed.

## Scenario B — an AI persona or AI feedback produced harmful content

1. Read the flag and confirm the output is harmful (abusive, discriminatory,
   unsafe advice, or seriously off-scenario).
2. Tell the **Study PIs**, who decide on an **IRB** adverse-event report.
3. Note which persona and which prompt/model versions were involved. The
   `ai.realtime_session` (voice), `ai.text_turn` (written) and `ai.scoring`
   audit events for the session carry the model and prompt versions; the
   `ai.guardrail` event's `stage` says which (`live_output`, `text_output`,
   `feedback`, ...). Open a fix: a persona prompt or guardrail
   change. A change of model or provider is an IRB and Information Security
   re-review trigger, so don't make one on your own.
4. Purge the session if the content should not be kept. Otherwise mark it
   reviewed.
5. If the same failure could happen again before it is fixed, consider pausing
   new interviews (stop the service) and tell students through the course.

## Scenario C — incident at the cloud AI provider

1. Forward the provider's notice to the **SOC** and **Information Security**.
2. If the incident could involve SES data (prompts, transcripts, credentials),
   **stop the SES service**:
   `sudo systemctl stop stakeholder-engagement-simulator`. Then rotate the AI
   credential and keep the service down until Information Security clears it.
3. Tell the **Study PIs**. A provider-side exposure of student data is also a
   matter for the IRB and the data-governance process.
4. Bring the service back only on Information Security's go-ahead. Record the
   timeline.

## Scenario D — suspected exposure of SES data or credentials

Treat any leaked secret, unexpected export, or unknown access to the identity
or research stores as a security incident. Tell **Information Security** and the
**SOC** at once, rotate affected credentials, and preserve the audit log
(`ses.audit` events) for the investigation. Research exports are recorded in
`research.export_log` with the requester and the named approver.

---

## Purging a flagged session

A purge only works through a flag, so there is always a recorded reason. It:

- empties the transcript and marks the session `purged_at`
- deletes the session's evaluations and retrieval telemetry
- deletes any **research copy** of the session
- marks every flag on the session `purged`. The flag stays as the incident record.
- emits the audit event `admin.session_purged` with the counts removed

From the browser console, signed in as the Support Owner:
```js
const csrf = document.cookie.match(/(?:^|; )(?:__Host-)?sis_csrf=([^;]+)/)[1];
await (await fetch(`/api/admin/flags/${FLAG_ID}/purge`, {method: 'POST', headers: {'X-CSRF-Token': csrf}})).json()
```

**Backups.** A purge does not reach existing database backups. They are
encrypted, and expire after `BACKUP_KEEP_DAYS` (14 by default, never longer than
the shortest retention window), so purged content is gone from every backup by
then. Note the purge date on the incident. If IRB or Information Security
decide it cannot wait, delete the backup files taken before the purge
(`/var/backups/stakeholder-engagement-simulator/`) and take a fresh one:
`sudo systemctl start stakeholder-engagement-simulator-backup.service`. If a
backup is ever restored, the restore procedure replays every purge since the
backup from the audit log (`app.jobs.after_restore`; see
[WPI_DEPLOY.md](WPI_DEPLOY.md#restoring-a-backup)), so a purged session cannot
come back.

---

## Roles in SES

Roles come from **Entra ID app-role assignments** and are re-synced from the
token at every sign-in. Granting or removing a role is an Entra change (the
enterprise application's *Users and groups*), never a database edit, and takes
effect at the person's next sign-in. Every sign-in records the roles it
carried in the audit log (`auth.login`, `roles`).

| Entra app role | SES role | Grants |
|---|---|---|
| `Student` | (student) | Use the simulator. Every user needs at least one role. |
| `Instructor` | `instructor` | Flag any session. |
| `StudyPersonnel` | `study_personnel` | Read and export consented research data. Only the IRB's approved study personnel. |
| `ExportApprover` | `export_approver` | Record a named, single-use, expiring approval for a research export. The approver cannot also be the exporter. |
| `SupportOwner` | `support_owner` | Review flags and purge sessions. |

To revoke access urgently (a compromised account), remove the assignment in
Entra **and** end the person's current SES sessions:
`DELETE FROM identity.auth_sessions WHERE user_id = '<account id>';`

---

## End of term

1. Set `RETENTION_TERM_END` in `.env` to the term's last day (and adjust
   `RETENTION_COURSE_GRACE_DAYS` if the course needs longer to settle grades
   outside SES). Run a `--dry-run` and check the counts look right.
2. Close or purge any open flags. Sessions under an open flag are held back
   from deletion.
3. Remove the term's Entra assignments (roster group, and staff roles that
   should not carry into the next term). Staff accounts are not deleted by the
   job.
4. After the grace period, confirm the run in `deletion_log` shows the course
   data deleted, and that backups older than the cutoff have rotated out.

