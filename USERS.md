# Venator Users, Roles, and Permissions

This document describes the identity model: the accounts the system ships with,
the roles those accounts hold, the permissions each role carries, how a request
presents its identity, and how to add a user.

The authority described here is enforced by `venator/api.py` and defined in
`config/policy.yaml`. No role name is checked in Python. Every permission
decision resolves to a list declared in the policy file.

## 1. The model in one paragraph

A request to the HTTP API may present an identity. That identity resolves to a
row in the `users` table, which names a role. The role's permission list is read
from the `roles:` section of `config/policy.yaml` at request time. Reads and
writes are then checked against those permissions, and reads are scoped so a
user sees only the transactions they are entitled to. A request that presents no
identity is treated as the anonymous operator and keeps full rights, which is
what allows the unauthenticated demo and the QA scripts to run unchanged.

## 2. The roles

Four roles ship, named in `config/policy.yaml`:

| Role | Who it represents |
|---|---|
| `student` | A campus wallet holder. Owns their own payments. |
| `guardian` | A parent or legal guardian linked to one student. Approves that student's guardian-gated transfers. |
| `staff` | A campus staff reviewer. Sees and resolves any case. |
| `admin` | The system operator. Everything staff can do, plus policy changes and user visibility. |

## 3. The permission matrix

Permissions are declared per role in `config/policy.yaml`. The matrix below is a
reading of that file; the file is the source of truth.

| Permission | student | guardian | staff | admin | Meaning |
|---|:---:|:---:|:---:|:---:|---|
| `view_queue` | yes | yes | yes | yes | Read the transaction queue and individual transactions, scoped. |
| `triage` | yes | no | yes | yes | Run the agent chain on a transaction. |
| `resolve_own` | yes | yes | no | no | Confirm or cancel a transaction belonging to the user's own linked student. |
| `resolve_any` | no | no | yes | yes | Confirm or cancel any transaction. |
| `guardian_approve` | no | yes | no | yes | Approve or decline a guardian-gated transfer. |
| `change_policy` | no | no | no | yes | Change a policy value through `PATCH /api/v1/policy`. |
| `view_audit` | no | no | yes | yes | Read the audit log. |
| `view_users` | no | no | no | yes | List and read user records. |

Reading the matrix:

- A `student` may see and act on their own payments only. They cannot read
  another student's payment, cannot resolve it, and cannot reach the guardian
  route.
- A `guardian` holds `guardian_approve` but is bound to the one student they are
  linked to. The guardian route succeeds for that student's transaction and is
  refused with `403` for any other student's transaction, even though the
  permission is held.
- A `staff` reviewer may see, triage, and resolve any case and read the audit
  log, but cannot change policy, cannot approve as a guardian, and cannot list
  users.
- An `admin` holds every permission, including `change_policy` and `view_users`.

## 4. How a request presents its identity

Two carriers are accepted, in this priority order:

| Carrier | Form | Example |
|---|---|---|
| HTTP header | `X-Venator-User: <user_id>:<token>` | `X-Venator-User: U-ADMIN:<token>` |
| Query parameter | `?user=<user_id>:<token>` | `?user=U-ADMIN:<token>` |

The header is used when a client can set headers. The query parameter exists so
that a plain `curl` invocation, which is how the API is exercised in this
project, can present an identity without extra tooling.

Behaviour when identity is presented:

- A valid `(user_id, token)` pair resolves to the user. The user's role
  permissions are applied to the request.
- An invalid pair (unknown user, wrong token, or inactive account) is refused
  with `401` and the body `{"error": "unauthorized: unknown user or invalid
  token"}`. An invalid identity is never quietly treated as the anonymous
  operator.

Behaviour when identity is absent:

- The request is the anonymous operator and keeps full rights, identical to the
  behaviour of the API before identity existed. This is deliberate: the demo
  dashboard and the QA scripts talk to the API without credentials.

Examples:

```bash
# Anonymous: full rights, unchanged behaviour.
curl -s http://127.0.0.1:8401/api/v1/transactions

# As an admin (query parameter form).
curl -s 'http://127.0.0.1:8401/api/v1/users?user=U-ADMIN:<token>'

# As a student (header form).
curl -s http://127.0.0.1:8401/api/v1/me \
  -H 'X-Venator-User: U-RAYAN:<token>'
```

## 5. Identity routes

| Method | Path | Who may call it |
|---|---|---|
| GET | `/api/v1/me` | Anyone. Returns the identity behind the request and the permissions its role holds. |
| GET | `/api/v1/users` | A role holding `view_users` (admin). A `403` otherwise. |
| GET | `/api/v1/users/{id}` | A role holding `view_users` (any record), or the user reading its own record. A `403` otherwise. |

Responses never contain a token. The user object returned by these routes has
the shape:

```json
{
  "user_id": "U-RAYAN",
  "display_name": "Rayan Haddad",
  "role": "student",
  "student_name": "Rayan",
  "email": "rayan@campus.example",
  "created_at": "2026-11-20 10:00:00",
  "active": true
}
```

## 6. Scoping of reads

A scoped read returns only the transactions the caller is entitled to. The scope
is derived from the caller's permissions, not from a role name:

| Caller | Scope | Sees |
|---|---|---|
| Role with `resolve_any` (staff, admin) | `all` | Every transaction. |
| Role with `resolve_own` (student, guardian) | `linked` | Only transactions whose `student` matches the user's `student_name`. |
| Role with neither | `none` | No transactions. |

Because a student and a guardian both link through the same `student_name`
column, a student sees their own rows and a guardian sees their linked student's
rows through one mechanism. A `linked` user whose `student_name` is unset sees
nothing, so a mis-seeded row cannot widen visibility.

Requesting a single transaction outside the caller's scope returns `403` with the
body:

```json
{"error": "forbidden: this user may not act on transaction TX-002, which is not theirs"}
```

## 7. What each role can and cannot do, in request terms

### student

Can: read their own transactions and queue, triage their own payments, resolve
(confirm or cancel) their own payments.

Cannot: read or act on another student's transaction (`403`), list users
(`403`), read the audit log (`403`), change policy (`403`), use the guardian
route (`403`).

### guardian

Can: read their linked student's transactions and queue, approve or decline that
student's guardian-gated transfers.

Cannot: act for a student they are not linked to (`403`), act on a payment
outside the held set, change policy (`403`), list users (`403`), triage
(`403` — the guardian role does not hold `triage`), read the audit log (`403`).

### staff

Can: read the whole queue, triage any transaction, resolve any transaction, read
the audit log.

Cannot: change policy (`403`), use the guardian route (`403` — staff does not
hold `guardian_approve`), list users (`403`).

### admin

Can: everything above, plus change a policy value through `PATCH
/api/v1/policy`, list and read users, and use the guardian route for any student.

### anonymous operator (no identity)

Can: everything, because the request carries no identity. This is the
pre-identity behaviour and is what the dashboard and QA scripts rely on. It is
not a role; it is the absence of one.

## 8. Seeded users

`python main.py build` seeds nine users covering all four roles. The
`student_name` column links a user to real rows in the dataset.

| user_id | display_name | role | student_name |
|---|---|---|---|
| `U-RAYAN` | Rayan Haddad | student | Rayan |
| `U-MAYA` | Maya Al-Farsi | student | Maya |
| `U-BILAL` | Bilal Othman | student | Bilal |
| `U-KAREEM` | Kareem Idris | student | Kareem |
| `U-NOOR` | Noor Al-Marzouqi | student | Noor |
| `U-FATIMA` | Fatima Al-Sayed | guardian | Bilal |
| `U-HESSA` | Hessa Al-Marzouqi | guardian | Noor |
| `U-STAFIA` | Stafia Mensah | staff | — |
| `U-ADMIN` | System Administrator | admin | — |

Consistency the seed maintains:

- Every student user's `student_name` matches a name that appears in
  `transactions`, so scoping has real rows to return.
- Every guardian user's `student_name` matches both `guardians.student_name` and
  the same student's transactions, so a guardian resolves to the right student
  through the same link the student role uses.
- `U-FATIMA` links to `Bilal`, whose dataset transfer `TX-015` (age 15, 150 AED
  to a peer) is held by the guardian rule, giving the guardian route a real case
  to act on.

## 9. Adding a user

A user is a row in the `users` table:

```sql
INSERT INTO users (user_id, display_name, role, student_name, email, api_token, active)
VALUES (:user_id, :display_name, :role, :student_name, :email, :api_token, 1);
```

- `user_id` is the identifier presented in `X-Venator-User` or `?user=`.
- `role` must name a role declared in `config/policy.yaml`. An unknown role
  resolves to no permissions; the account can authenticate and read `/me` but
  can change nothing.
- `student_name` links the account to transactions. It is required for a student
  and for a guardian (set it to the linked student's name). It is `NULL` for
  staff and admin.
- `api_token` is generated with `secrets.token_hex`. `python main.py build`
  generates a fresh token per user per build and writes it to the row. The
  application never prints a token.

To add a user without rebuilding the dataset, insert the row directly. To add a
role, add an entry under `roles:` in `config/policy.yaml`; no code change is
needed.

## 10. Security honesty

The token scheme here is demonstration-grade. It exists to show that scoped
authority works end to end, and it should be read as exactly that.

What it does: each user row stores one long-lived shared secret, generated with
`secrets.token_hex`. Authentication compares the presented secret to the stored
one with `hmac.compare_digest`, a constant-time comparison. An unknown user, a
wrong token, or an inactive account all fail closed, and a failure is never
allowed to fall through to the full-rights anonymous operator.

What it deliberately does not do, and what a production system would require
instead:

- **Storage.** The token is stored in plaintext. Production would store a salted
  password hash (for example Argon2id or bcrypt) or, for machine credentials, a
  hashed API key, never a recoverable secret.
- **Transport.** The scheme assumes a trusted channel. The demonstration server
  binds to `127.0.0.1` only. Production would require TLS and refuse credentials
  over plaintext.
- **Lifetime.** Tokens never expire and cannot be rotated or revoked short of
  deleting the row. Production would use short-lived sessions or signed tokens
  with an expiry, plus explicit rotation and revocation.
- **Binding.** A token is not bound to a client, device, or session, and there
  is no replay protection. Production would bind credentials to a session and
  use standard session or OAuth 2.0 / OIDC flows.
- **Rate limiting and lockout.** There is none. Production would throttle
  authentication attempts and lock or alarm on repeated failure.
- **Audit of authentication.** Failed authentications are not logged. Production
  would record them.

This is not presented as secure authentication. It is a clear, honest,
dependency-free mechanism for a demonstration system, and the gap to production
is listed above on purpose.
