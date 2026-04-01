# Release Checklist

- Verify the repo deploy commands still work:
  - `scripts/deploy_host.sh`
  - `scripts/deploy_hidden_server.sh`
  - `scripts/start_client.sh`
- Verify no real tokens are present in tracked files
- Verify `host/storage/` is empty or absent
- Verify `TWOMAN_TRACE` is not enabled in production
- Verify broker health responds on the configured public base URI, for example `/api/v1/telemetry/health`
- Verify SOCKS egress through the helper
- Verify HTTP egress through the helper
