# Host App Mappings

This document tracks the public-host naming pattern used by the deploy
automation without checking private host names into the repo.

Only Persian-transliterated names should remain active on the public host.

## Stable Fallback Defaults

- Passenger app name: `rahkar`
- Public base URI: `/rahkar`
- Passenger app root: `$CPANEL_HOME/rahkar`
- Node selector app root: `$CPANEL_HOME/rahkar_node`
- Node selector public base URI: `/rahkar-node`
- Node selector admin script: `rahkar_negahban.php`

When camouflage generation is enabled, the public base URI may be replaced with
a generated random Persian-style path. The defaults above are only the stable
fallbacks used when randomized deployment is not requested.

## Mapping Guidance

- public route: `/<persian-public-base>`
- Passenger app root: `$CPANEL_HOME/<persian-app-root>`
- Node selector app root: `$CPANEL_HOME/<persian-node-root>`
- admin helper script: `<persian-admin-name>.php`

Do not commit real host brand names, domains, or live public routes here.
