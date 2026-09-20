# Trusty-Ka security/UI update

## Backend
- Added expiring signed auth tokens (7-day lifetime).
- Added PBKDF2-SHA256 password hashing; legacy SHA-256 passwords upgrade on successful login.
- Removed insecure password-reset response that returned a temporary password. The reset endpoint now asks users to contact support until an email/SMS reset channel is configured.
- Added login, registration, booking, review, complaint and contact-view rate limits.
- Added security response headers and removed wildcard CORS.
- Added authorization checks for provider profile edits, provider booking access, booking accept/reject/complete, notifications, provider lookup by user, and admin warning actions.
- Public provider profile access now exposes only approved providers.
- Client booking-status responses no longer expose the client's phone number or booking notes.
- Added review star/text validation and upload-size limits for provider images.
- Removed the duplicate ongoing-service background checker.
- Removed the admin password from startup logs.

## Frontend
- Fixed spacing between `PATA "SERVICE"` and `POA` in the hero heading.
- Removed the admin secret from frontend source; admin access is still protected by backend authentication.
- Updated password minimum to 8 characters.
- Updated the forgot-password UI to match the safer backend behavior.
- Routed admin provider warnings through the protected admin endpoint.
- Removed redundant suspension notification calls.
- Added HTML escaping to several user-generated provider/review fields to reduce XSS risk.
