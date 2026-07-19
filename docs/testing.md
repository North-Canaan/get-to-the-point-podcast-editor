# Regression testing

Run the complete local suite before every deployment:

```sh
.venv/bin/pytest -q
npm test
.venv/bin/ruff check .
```

The Python suite covers API behavior, state recovery, RSS generation and delivery,
authentication failures, storage upload confirmation, SSRF/rate-limit protections,
and the transcript/highlight pipeline. The Node suite executes the browser's actual
size-estimation and editing-path logic, including large sources, long selections,
invalid highlight ranges, and output size guardrails.

## Production smoke test

The deployed RSS chain can be checked without storing a private feed URL in source:

```sh
PRODUCTION_SMOKE_FEED_URL='https://…/private-feed/….xml' \
  .venv/bin/pytest -q tests/test_production_smoke.py
```

It parses the live feed and checks the three newest enclosure URLs, redirects,
content types, and content lengths. Configure this value as a protected CI secret
if the test should run after deployments.
