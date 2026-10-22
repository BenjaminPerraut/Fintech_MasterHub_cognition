# SSO: swapping the stub for Entra ID or Okta

The app only depends on `IdentityProvider` (`app/auth/provider.py`):

```python
class IdentityProvider(ABC):
    def login_context(self) -> dict: ...                       # what the login page shows
    async def authenticate(self, request) -> Identity: ...     # handle the IdP callback
```

`Identity(subject, email, name, groups)` is all the rest of the app sees. Roles are derived from
`groups` via `GROUP_TO_ROLE` in `app/auth/rbac.py`, exactly one role per person.

## Entra ID / Okta (OIDC authorization-code + PKCE)

1. Register an app (Entra: App registrations; Okta: OIDC Web app). Redirect URI `https://<host>/auth/callback`.
2. Emit a `groups` claim (Entra: "Groups claim" → security groups, values are object IDs; Okta: groups claim filter `mh-*`).
3. Implement `OidcProvider(IdentityProvider)`:
   - `login_context()` returns `{"provider": "Microsoft Entra ID", "stub": False, "start_url": "/auth/start"}`.
   - Add `GET /auth/start` that stores `state`, `nonce`, PKCE `code_verifier` in the session and redirects to the authorize endpoint.
   - `authenticate()` exchanges the code, validates the ID token (issuer, audience, `exp`, `nonce`, signature against JWKS) and returns `Identity(sub|oid, email, name, groups)`.
   - Use a maintained library (e.g. `authlib`) rather than hand-rolled JWT validation.
4. Map real group IDs in `GROUP_TO_ROLE` (or load them from env).
5. Set `MASTERHUB_IDP=entra|okta`, `MASTERHUB_SECRET_KEY`, `MASTERHUB_HTTPS_ONLY=1`; client secret from your secret manager, never the repo.
6. MFA and conditional access are enforced at the IdP; the app keeps its own 8h absolute session cap.

Nothing else changes: `current_user`, `require_perm`, four-eyes and the audit log already key off `subject`.

Deprovisioning: users are JIT-provisioned at login and re-synced (role) at every login. For immediate
offboarding, add SCIM or a nightly sync that sets `users.active = 0` — enforced on the next request.
