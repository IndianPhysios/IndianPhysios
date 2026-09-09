# Indian Physios V8 Production

Production-oriented Flask build for Indian Physios. Local development uses SQLite; production can use PostgreSQL through `DATABASE_URL`.

## Local
`./run_mac.sh` then open http://127.0.0.1:5050

## Production environment
Required: `SECRET_KEY` (32+ random characters), `DATABASE_URL` (PostgreSQL), `APP_BASE_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `ADMIN_EMAIL`.

For Render, use the included `Procfile`. Do not commit the database, uploaded images, virtual environment, or secrets.

The app stores profile image bytes in the database so production restarts do not depend on local disk. Existing local profile images are imported into the database at startup.
