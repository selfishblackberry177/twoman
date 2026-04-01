# Architecture

## Summary

Twoman is designed for environments where:
- the public host must remain in the traffic path
- the public host is a shared cPanel/LiteSpeed environment
- the hidden server can make outbound HTTPS requests to the host
- end-user applications need a local HTTP/SOCKS5 proxy

## Data Path

1. An application talks to the local helper.
2. The helper speaks Twoman frames to the public broker at a fully configured base URI such as
   `/api/v1/telemetry`, with relative route templates such as `/{lane}/{direction}`.
3. Preferred shared-host deployments use internal application-server integration such as Passenger WSGI.
4. The hidden agent maintains a reverse session to the broker.
5. The hidden server opens the real outbound TCP connection.

## Lanes

External lanes:
- `ctl`
- `data`

Internal scheduling classes:
- `ctl`
- `pri`
- `bulk`

The external `data` lane carries both `pri` and `bulk` traffic. `FRAME_DATA` bulk frames are marked with `FLAG_DATA_BULK` so the broker can preserve scheduling intent.

## Authentication

Each public request now prefers:
- `Authorization: Bearer <token>`
- `Cookie: twoman_role=...; twoman_peer=...; twoman_session=...`

Legacy `X-Relay-Token` and `X-Twoman-*` headers are deprecated compatibility fallbacks.
The token remains the shared bearer credential. Peer and session cookies provide routing identity, not the secret.

## Why The Host Broker Exists

The broker exists because:
- PHP is too expensive for the hot path
- shared-host public ports are unavailable
- the hidden server cannot accept direct inbound public traffic in the required topology

The broker is:
- asyncio-based
- deployable behind Passenger WSGI or a compatibility daemon
- able to use Unix domain sockets for host-internal control traffic when the deployment supports it

## Host Constraints

Twoman is intentionally shaped around shared-host reality:
- response streaming works better than aggressive request-body transport
- helper downlinks use streamed HTTP/1.1
- binary lane traffic defaults to `image/webp` so intermediaries see a standard media type
- larger uploads and larger browser workloads are the practical ceiling

## Production Reality

Twoman is best suited for:
- Telegram
- lighter browsing
- constrained relay scenarios where “host must stay in path” is more important than maximum throughput

It is not a substitute for a direct tunnel or a VPS-based relay.
