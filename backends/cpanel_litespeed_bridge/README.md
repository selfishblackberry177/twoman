# cPanel LiteSpeed Bridge Backend

This is the stable Twoman backend for hosts that do not provide a usable app
runtime.

It uses:

- LiteSpeed `.htaccess [P]` reverse proxy
- PHP bootstrap/watchdog
- localhost Python broker

Use this backend when:

- Passenger/Application Manager is unavailable
- the host does not expose a usable Python or Node app runtime
- the old shared-host trick is the only viable execution surface

Deploy with:

- `scripts/deploy_host.sh`
