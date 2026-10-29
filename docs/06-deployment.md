# Deployment and operations

Two supported layouts:

- **Server install** on Ubuntu 24.04 LTS (Python 3.12) with PostgreSQL, Gunicorn under systemd, Nginx and cron.
- **Docker Compose** with three services: PostgreSQL, the web application and a job runner.

Both read their settings from a `.env` file. Start from [`.env.example`](../.env.example).

## Server install

### 1. Packages and account

```bash
sudo apt install python3 python3-venv postgresql nginx
```

```bash
sudo useradd --system --no-create-home --home-dir /srv/cut-acquisitions cutacq
```

```bash
sudo mkdir -p /srv/cut-acquisitions && sudo chown cutacq:cutacq /srv/cut-acquisitions
```

### 2. Database

```bash
sudo -u postgres psql -c "CREATE ROLE cutacq LOGIN PASSWORD 'choose-a-strong-password';"
```

```bash
sudo -u postgres psql -c "CREATE DATABASE cutacq OWNER cutacq;"
```

### 3. Code and dependencies

```bash
sudo -u cutacq git clone https://github.com/takundanyangani/Cut_Acquisition_System.git /srv/cut-acquisitions
```

The files in `deploy/` assume this path.

```bash
cd /srv/cut-acquisitions && sudo -u cutacq python3 -m venv .venv && sudo -u cutacq .venv/bin/pip install -r requirements.txt
```

### 4. Configuration

```bash
sudo -u cutacq cp .env.example .env && sudo -u cutacq chmod 600 .env
```

Fill in at least `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `ACQ_SITE_URL`, `DATABASE_URL`, the `EMAIL_*` values and `ACQ_UPLOAD_ROOT`. Generate the secret key with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

### 5. Database tables, static files and the first administrator

```bash
sudo -u cutacq .venv/bin/python manage.py migrate
```

```bash
sudo -u cutacq .venv/bin/python manage.py collectstatic --noinput
```

```bash
sudo -u cutacq .venv/bin/python manage.py createsuperuser
```

```bash
sudo -u cutacq .venv/bin/python manage.py check --deploy
```

`check --deploy` should report at most two warnings, both decisions for CUT's network team: `security.W005` (HSTS for sub-domains, set `DJANGO_HSTS_INCLUDE_SUBDOMAINS=true` if every `cut.ac.zw` sub-domain serves HTTPS) and `security.W021` (HSTS preload).

### 6. Gunicorn, Nginx and scheduled jobs

```bash
sudo cp deploy/cutacq.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now cutacq
```

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/cutacq && sudo ln -s /etc/nginx/sites-available/cutacq /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx
```

```bash
sudo mkdir -p /var/log/cutacq && sudo chown cutacq /var/log/cutacq && sudo -u cutacq crontab deploy/crontab
```

Edit the host name and certificate paths in the Nginx file first. With Nginx in front, set `DJANGO_BEHIND_PROXY=true` and `ACQ_TRUST_X_FORWARDED_FOR=true`.

### 7. Check email

```bash
sudo -u cutacq .venv/bin/python manage.py sendtestemail your.name@cut.ac.zw
```

## Docker Compose

```bash
cp .env.example .env
```

Set `POSTGRES_PASSWORD` and the other values, then:

```bash
docker compose up -d --build
```

```bash
docker compose exec web python manage.py createsuperuser
```

The web service listens on `127.0.0.1:8000` only. Put Nginx (or the CUT reverse proxy) in front for TLS. The `jobs` service runs `deploy/scheduler.py`, which sends email every 5 minutes, reminders at 07:30 on weekdays and retention at 02:15. Uploads live in the `uploads` volume and the database in `pgdata`.

## Scheduled jobs

| Command | When | What it does |
| --- | --- | --- |
| `deliver_emails` | Every 5 minutes | Sends queued email and retries failures (up to 6 attempts) |
| `send_reminders` | 07:30, Monday to Friday | Reminds officers about requests past their stage target, and technicians about due Procurement follow-ups. Skips public holidays |
| `apply_retention` | 02:15 daily | Moves closed requests to the archive after their retention period; prunes sign-in records older than a year and sent emails older than 90 days |
| `verify_audit_chain` | 02:45 on Sundays | Checks every audit chain; exits with status 1 and prints the broken entries if any |

## First-time setup checklist

1. Sign in to `/admin/` with the superuser account.
2. Add campuses and departments (**Organisation and system**).
3. Load the public holidays for this year and next (**Holidays**). Check them against the Government Gazette; moved holidays need adding by hand.
4. Review the asset categories. Twelve come with the system.
5. Import staff: `python manage.py import_users staff.csv` (dry run), then add `--commit`. See [07-integration.md](07-integration.md) for the file format.
6. Grant workflow roles on each officer's user page, or with the `roles` column in the import file.
7. Import the existing asset register: `python manage.py import_assets assets.csv`, then `--commit`.
8. Ask the ICT Director to confirm the stage targets in [03-workflows.md](03-workflows.md) and set any changes in `.env`.

## Backups and restore

Back up the database and the uploads folder together, at least daily. The audit chain and attachments refer to each other.

```bash
pg_dump --format=custom --file=/backups/cutacq-$(date +%F).dump cutacq
```

```bash
rsync -a /srv/cut-acquisitions/var/uploads/ /backups/uploads/
```

To restore, stop the service, restore both, then verify:

```bash
pg_restore --clean --if-exists --dbname=cutacq /backups/cutacq-2026-10-29.dump
```

```bash
sudo -u cutacq .venv/bin/python manage.py verify_audit_chain
```

## Upgrades

```bash
cd /srv/cut-acquisitions && sudo -u cutacq git pull && sudo -u cutacq .venv/bin/pip install -r requirements.txt
```

```bash
sudo -u cutacq .venv/bin/python manage.py migrate && sudo -u cutacq .venv/bin/python manage.py collectstatic --noinput && sudo systemctl restart cutacq
```

Take a backup before each upgrade. Run the test suite on a staging copy first (see [11-testing.md](11-testing.md)).

## Monitoring

- `GET /healthz` returns `{"status": "ok"}` with status 200 when the database answers, and 503 when it does not. It needs no sign-in and reveals nothing else.
- Application logs go to standard output: `journalctl -u cutacq`. Set `DJANGO_ADMINS` to email server errors to the systems team.
- Failed emails show in the administration site under **Outbound emails**, with the SMTP error and a retry action.
- Job output goes to `/var/log/cutacq/jobs.log`.

## Configuration reference

### Core

| Variable | Default | Meaning |
| --- | --- | --- |
| `DJANGO_DEBUG` | `false` | Never `true` in production |
| `DJANGO_SECRET_KEY` | none (required) | Long random string. Changing it signs everyone out and voids password reset links |
| `DJANGO_ALLOWED_HOSTS` | none | Comma-separated host names |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | none | Full origins, for example `https://acquisitions.cut.ac.zw` |
| `ACQ_SITE_URL` | `http://localhost:8000` | Origin used in email links, with no path |
| `DJANGO_SCRIPT_NAME` | empty | Sub-path when the app runs inside the CUT portal, for example `/ict-acquisitions` |
| `DATABASE_URL` | `sqlite:///db.sqlite3` | `postgres://user:password@host:5432/name` in production |
| `DATABASE_CONN_MAX_AGE` | `60` | Seconds to keep database connections open |
| `DJANGO_TIME_ZONE` | `Africa/Harare` | |
| `DJANGO_STATIC_ROOT` | `./staticfiles` | Where `collectstatic` writes |
| `ACQ_UPLOAD_ROOT` | `./var/uploads` | Attachment storage. Back it up |
| `DJANGO_LOG_LEVEL` | `INFO` | |
| `DJANGO_ADMINS` | empty | Comma-separated addresses that receive server error emails |

### Transport and cookies

| Variable | Default | Meaning |
| --- | --- | --- |
| `DJANGO_BEHIND_PROXY` | `false` | Trust `X-Forwarded-Proto` and `X-Forwarded-Host` from the proxy |
| `ACQ_TRUST_X_FORWARDED_FOR` | `false` | Take the client IP from `X-Forwarded-For` (lockout and audit). Only behind a proxy you control |
| `DJANGO_SECURE_SSL_REDIRECT` | `true` when not in debug | Redirect HTTP to HTTPS |
| `DJANGO_SECURE_COOKIES` | `true` when not in debug | Secure flag on session and CSRF cookies |
| `DJANGO_HSTS_SECONDS` | `31536000` when not in debug | HTTP Strict Transport Security |
| `DJANGO_HSTS_INCLUDE_SUBDOMAINS` | `false` | |
| `DJANGO_HSTS_PRELOAD` | `false` | |
| `SESSION_COOKIE_AGE` | `28800` | Longest session in seconds (8 hours). Sessions also end when the browser closes |

### Email

| Variable | Default | Meaning |
| --- | --- | --- |
| `EMAIL_HOST`, `EMAIL_PORT` | `localhost`, `25` | CUT mail server |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | empty | |
| `EMAIL_USE_TLS`, `EMAIL_USE_SSL` | `false` | Use one, not both |
| `EMAIL_TIMEOUT` | `15` | Seconds |
| `DEFAULT_FROM_EMAIL` | `ICT Acquisitions <noreply@localhost>` | Sender address |
| `ACQ_EMAIL_DELIVERY` | `thread` | `thread` sends after commit without delaying the page; `cron` leaves sending to `deliver_emails` |

### Organisation

| Variable | Default |
| --- | --- |
| `ACQ_ORG_NAME` | `Chinhoyi University of Technology` |
| `ACQ_ORG_SHORT_NAME` | `CUT` |
| `ACQ_UNIT_NAME` | `ICT Services` |
| `ACQ_HELPDESK_CONTACT` | `the ICT Helpdesk` |
| `ACQ_CURRENCY` | `USD` |

### Security rules

| Variable | Default | Meaning |
| --- | --- | --- |
| `ACQ_SIGNATURE_REQUIRES_PASSWORD` | `true` | Ask for the password on signing steps |
| `ACQ_IDLE_TIMEOUT_MINUTES` | `30` | Sign out after this long without a request |
| `ACQ_LOGIN_MAX_FAILURES` | `5` | Failures per account before lockout |
| `ACQ_LOGIN_MAX_FAILURES_PER_IP` | `20` | Failures per IP address before lockout |
| `ACQ_LOGIN_LOCK_MINUTES` | `15` | Lockout window |
| `ACQ_UPLOAD_MAX_MB` | `10` | Largest attachment |

### Workflow timing and retention

| Variable | Default | Meaning |
| --- | --- | --- |
| `ACQ_TARGET_*` | see [03-workflows.md](03-workflows.md) | Working-day target per stage |
| `ACQ_FOLLOW_UP_INTERVAL_DAYS` | `5` | Default gap between Procurement follow-ups |
| `ACQ_RETENTION_EQR_DAYS` | `183` | CUT ICT 38: one semester |
| `ACQ_RETENTION_ALC_DAYS` | `365` | CUT ICT 40: one year |
| `ACQ_RETENTION_RPL_DAYS` | `365` | CUT ICT 42: one year |
| `ACQ_RETENTION_VOUCHER_DAYS` | `365` | CUT ICT 40, 8.1: one year for issue vouchers |

### Directory sign-in (optional)

`LDAP_SERVER_URI`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_USER_BASE_DN`, `LDAP_USER_FILTER`, `LDAP_START_TLS`. See [07-integration.md](07-integration.md).

### Gunicorn

`GUNICORN_BIND` (`0.0.0.0:8000`), `GUNICORN_WORKERS` (twice the CPU count plus one, at most 9), `GUNICORN_THREADS` (`2`), `GUNICORN_TIMEOUT` (`60`), `GUNICORN_FORWARDED_ALLOW_IPS` (`127.0.0.1`).
