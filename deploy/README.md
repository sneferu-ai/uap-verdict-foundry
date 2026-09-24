# Production deployment

This profile runs one Verdict Foundry instance behind a TLS-terminating Caddy
proxy. It preserves the single-operator boundary and the single-writer SQLite
topology.

1. Install Python 3.11+, FFmpeg/ffprobe, bubblewrap, Caddy, and the built wheel
   into `/opt/verdict-foundry/venv` on a Linux host with unprivileged user
   namespaces enabled.
2. Create the locked service account `uapvf` and `/etc/uapvf/uapvf.env` mode
   `0600`. Start from `.env.example`; use random operator, signing, and HMAC
   secrets. Keep strict references and live lineage mode.
3. Install `uapvf.service` in `/etc/systemd/system/`, run `uapvf db init` and
   `uapvf references fetch` as the service account, then enable the service.
4. Set `UAPVF_HOSTNAME` for Caddy and install `Caddyfile`. The public proxy
   deliberately hides `/metrics`; scrape it over the private loopback or host
   monitoring network.
5. Gate traffic on `/readyz`. Schedule `uapvf retention run` daily and
   `uapvf backup` at the required recovery point objective. Perform a restore
   drill before promotion; a backup that has not been restored is not a proof.

The service unit writes only `/var/lib/uapvf`, drops Linux capabilities, and
restarts on failure. Per-upload media parsing uses bubblewrap inside the
service; deployment must fail readiness testing if bubblewrap cannot create its
case-only namespace.
