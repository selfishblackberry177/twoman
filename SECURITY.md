# Security Policy

## Supported versions

Twoman is maintained as a rolling project. The latest tagged release is the
supported public version.

## Reporting a vulnerability

Do not open a public GitHub issue for live tokens, host access details, broker
paths, or exploit details.

Instead:

1. email the maintainer directly
2. include affected version, environment, and reproduction steps
3. include sanitized logs only

If the issue involves a deployed public host or hidden server, rotate any live
tokens before sharing additional diagnostics.

## Scope

Security-sensitive areas include:

- broker authentication and session handling
- hidden-agent reachability and upstream-proxy routing
- local helper proxy exposure
- Android VPN and proxy runtime behavior
- desktop shared-proxy exposure and tunnel sidecars

## Public repo rules

- never commit live tokens or passwords
- never commit private cPanel or hidden-server host details
- keep private operational notes in non-public handoff material
