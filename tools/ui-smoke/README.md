# UI smoke harness (optional)

`node --check` proves the panel's JavaScript parses. It does not prove the
panel renders, calls the right endpoints, or survives an error path. These two
scripts close most of that gap without a browser: they load the real
`index.html` and `app.js` into [jsdom](https://github.com/jsdom/jsdom), stub
`fetch` with payloads shaped like the real management API, and assert that each
section renders, dialogs open and close, and the expected requests are sent.

They are **not** part of CI and are not a visual or accessibility audit. jsdom
has no layout engine, so spacing, contrast, and overflow are not checked here —
`agent_gateway/tests/test_ui_assets.py` covers the static design rules, and a
human still has to look at the panel in Home Assistant.

```bash
cd tools/ui-smoke
npm install
npm test
```

`panel_smoke.mjs` walks every navigation entry and opens the connection, policy,
and approval dialogs. `panel_paths.mjs` covers vault setup, a wrong master
password, the auto-unlock warning, approval submission, and policy validation.
`access_smoke.mjs` renders the Agent Access section, issues a temporary
session, verifies the one-time handoff dialog, and revokes it through the
confirmation dialog. All three fail with a non-zero exit code if the panel logs
a runtime error.

`agent_page_smoke.mjs` renders the agent-facing handoff page in jsdom with a
stubbed redeem response and checks that the token, base URL, MCP endpoint, and
one-time warning render while the URL fragment is stripped.
