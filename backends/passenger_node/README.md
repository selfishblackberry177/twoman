# Passenger Node Backend

This backend is the planned high-upside runtime for Passenger-capable cPanel
hosts.

Why it exists:

- Node is a better fit for long-lived bidirectional traffic than WSGI
- if LiteSpeed + Passenger Node.js + WebSocket upgrade works, this backend is
  the strongest candidate for reducing Twoman request churn on shared hosting

Current status:

- runtime not yet proven on the new host
- proof app scaffold exists for fast registration and smoke tests
- WebSocket production transport is not implemented yet

Files:

- `backends/passenger_node/app.js`
- `backends/passenger_node/package.json`
