# Passenger Python Backend

This backend targets cPanel hosts that expose a real Passenger/Application
Manager surface.

Goal:

- replace PHP bootstrap + localhost-broker hacks with a Passenger-managed
  Python application on the public host

Current status:

- host app registration is proven
- host health endpoint is proven
- end-to-end Twoman traffic is not yet production-ready

Known issue:

- the current in-memory broker model does not yet map cleanly onto the
  Passenger/LiteSpeed process model under real traffic, even with:
  - `LSAPI_CHILDREN=1`
  - `LSAPI_AVOID_FORK=1`

Files:

- `host/passenger_python/broker_app.py`
- `host/passenger_python/passenger_wsgi.py`
- `backends/passenger_python/proof_app.py`
- `scripts/deploy_host_passenger.sh`
