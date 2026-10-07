# Linux home-server deployment with Docker and Caddy

This setup runs two containers: Gunicorn/Django serves the app and static files; Caddy provides local HTTPS. SQLite stays in `./data/db.sqlite3` on the Linux host. Caddy's local certificate authority stays in named Docker volumes. Rebuilding an image does not erase either.

## 1. Prepare the server

Install Docker Engine and its Compose plugin using [Docker's Linux instructions](https://docs.docker.com/engine/install/). These commands assume your account can run `docker`; otherwise prefix Docker commands with `sudo`. Access to the Docker daemon is effectively administrative access.

Give the server a fixed/reserved LAN IPv4 address. The example below uses `192.168.1.50`; replace it with yours. Ports 80 and 443 must be free on that address.

Clone this repository onto the server and run all commands from its directory. Then:

```sh
cp .env.example .env
chmod 600 .env
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
```

Edit `.env`: set `LAN_IP` to your server's LAN address, keep `BUDGET_HOST=budget.home.arpa` unless you prefer another local hostname, and paste the generated value into `SECRET_KEY`. Keep that secret stable during upgrades. Do not commit `.env` or show the output of `docker compose config` publicly because it contains resolved secrets.

Create the app's persistent directory with permissions for its non-root container user:

```sh
sudo install -d -m 700 -o 10001 -g 10001 data
```

The UID/GID `10001` matches the Dockerfile. Do not use `chmod 777`. Use a local disk, not a network share, for SQLite.

## 2. Make the local name resolve

Add a local DNS record in your router, Pi-hole or other home DNS server:

```text
budget.home.arpa → 192.168.1.50
```

If your router cannot do this, add `192.168.1.50 budget.home.arpa` to each computer's hosts file. Phones generally need working local DNS. No public DNS or router port forwarding is required. Devices using an external/private DNS service may need to use your home DNS to resolve this name.

Only the chosen LAN address publishes ports 80 and 443. Do not configure WAN forwarding. Keep the server firewall restricted to your LAN; Docker-published ports have their own firewall behaviour, so do not assume a generic UFW rule alone blocks them. See [Docker's firewall documentation](https://docs.docker.com/engine/network/packet-filtering-firewalls/).

## 3. Choose an existing or new budget

For an **existing budget**, stop the old application and copy its actual SQLite file to the Linux server as `data/db.sqlite3`, then:

```sh
sudo chown 10001:10001 data/db.sqlite3
sudo chmod 600 data/db.sqlite3
```

Use the database that holds your real budget, not the separate fictional preview database. Keep your original backup. Copy SQLite only while the old app is stopped, or use SQLite's backup API. Existing users and password hashes are retained; a new server secret means users log in again.

For a **new budget**, leave the data directory empty. Migrations create the database.

## 4. Build, validate and initialise

```sh
docker compose config --quiet
docker compose build --pull app
docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose run --rm app python manage.py migrate
```

For a new database only, create your administrator login:

```sh
docker compose run --rm app python manage.py createsuperuser
```

Start the service:

```sh
docker compose up -d
docker compose ps
```

Caddy waits for the app's health check to pass. Visit `https://budget.home.arpa` after trusting the certificate below. The application port is not published; do not add a port mapping for app:8000, as Django trusts headers supplied by Caddy on this private network.

## 5. Trust Caddy's local certificate authority

Caddy uses `tls internal`, which issues certificates from a private local authority rather than a public certificate provider. Your devices must trust its **public root certificate**. Copy it from the running container:

```sh
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
```

Transfer this certificate to your own devices using a trusted local route. Install it as a trusted root certificate authority:

- **macOS:** import into the System keychain using Keychain Access and explicitly trust it for SSL.
- **Windows:** import into Trusted Root Certification Authorities for your device.
- **Debian/Ubuntu:** copy it to `/usr/local/share/ca-certificates/12andme.crt`, then run `sudo update-ca-certificates`.
- **iPhone/iPad:** install the certificate profile and enable full trust under Settings → General → About → Certificate Trust Settings.
- **Android:** install it as a CA certificate through the device's security settings. Menu names and browser support vary.
- Some browsers, including some Firefox configurations, use their own certificate store; import the same root there if needed.

Trust only the certificate you obtained from your own Caddy container. Do not share the authority's private key. Do not bypass a browser certificate warning as a substitute for configuring trust.

Keep the `caddy_data` volume. Deleting it causes a new authority to be generated and devices will need to trust a new certificate. See [Caddy's local HTTPS documentation](https://caddyserver.com/docs/automatic-https#local-https).

## 6. Verify the deployment

```sh
docker compose ps
docker compose logs --tail=50 app caddy
docker compose run --rm app python manage.py check --deploy
```

Confirm login, the dashboard and admin CSS, CSV import, and logout work from a trusted LAN device. Confirm HTTP redirects to HTTPS. The deployment check intentionally leaves domain-wide HSTS/preload recommendations unset; this is a private home service, not a public domain to preload.

Five failed logins cause a 15-minute lockout. Caddy replaces the client-IP header before forwarding, so lockouts use the connecting device's address. Do not place another proxy in front without reviewing trusted-header handling.

## 7. Back up and upgrade without losing data

Stop the app before making a straightforward file-copy backup. From the repository directory:

```sh
docker compose stop app
sudo install -d -m 700 backups
sudo cp -p data/db.sqlite3 "backups/budget-$(date +%Y%m%d-%H%M%S).sqlite3"
```

Keep backups private and copy them off the server periodically. Save `.env` securely as well. For disaster recovery, back up Caddy's named volumes using your Docker backup tool; they contain the authority's private key and must be protected like a secret. `backups/` is excluded from Git and the Docker build context.

After the backup, update the code and rebuild:

```sh
git pull
docker compose build --pull app
docker compose pull caddy
docker compose run --rm app python manage.py migrate
docker compose up -d
docker compose ps
```

Run these one step at a time. If the build or migration fails, stop and inspect it instead of blindly proceeding. Migrations are explicit, not automatically run on every container restart. They preserve data unless a future migration deliberately changes/removes it; review release instructions.

Never delete `data/`, run Django `flush`, or use `docker compose down -v` during an upgrade. `down -v` deletes the named Caddy volumes, although the host's bind-mounted database remains. A normal `docker compose down` retains persistent data.

For rollback, stop the app, restore the database backup to `data/db.sqlite3` with UID/GID 10001, restore the matching old code/image, and start it. Do not assume older code understands a newer database schema. Restoring a backup discards changes made since that backup.

## Troubleshooting

- **App unhealthy:** inspect app logs. Check migrations, `.env`, and ownership of `data/`.
- **Permission denied on database:** confirm the directory and database belong to UID/GID 10001 and the host disk is writable.
- **502 from Caddy:** app may be starting, stopped for an upgrade, or unhealthy.
- **Certificate warning:** check DNS, hostname and certificate trust. Access using the configured hostname rather than an IP address.
- **Redirect loop:** use the supplied private network/Caddy header settings; don't expose the app directly.
- **Address already in use:** another service owns port 80/443. Resolve that conflict rather than starting a second proxy on those ports.

The container files were added in an environment without Docker Engine, so the image build and Caddy runtime validation must be completed on the Linux server. Application tests cover the trusted-header configuration; they do not replace an end-to-end deployment check.
