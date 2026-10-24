# UI smoke harnesses

`node --check` proves the panel's JavaScript parses. It does not prove the
panel renders, calls the right endpoints, or survives an error path. These two
scripts close most of that gap without a browser: they load the real
`index.html` and `app.js` into [jsdom](https://github.com/jsdom/jsdom), stub
`fetch` with payloads shaped like the real management API, and assert that each
section renders, dialogs open and close, and the expected requests are sent. The
five harnesses cover the management panel, error paths, guided Agent Access,
the agent handoff page, and release notices.

They run in the CI UI job but are not a visual or accessibility audit. jsdom
has no layout engine, so spacing, contrast, and overflow are not checked here —
`agent_gateway/tests/test_ui_assets.py` covers the static design rules, and a
human still has to look at the panel in Home Assistant.

```bash
cd tools/ui-smoke
npm ci
npm test
```

`panel_smoke.mjs` walks every navigation entry and opens the connection, policy,
and approval dialogs. `panel_paths.mjs` covers vault setup, a wrong master
password, the auto-unlock warning, approval submission, and policy validation.
`access_smoke.mjs` renders guided and advanced Agent Access, verifies automatic
scope selection and base-address normalization, checks the copyable AI prompt
and fresh-install address suggestion, issues a temporary session, and revokes it
through the confirmation dialog. Every harness fails with a non-zero exit code
if the panel logs a runtime error.

`agent_page_smoke.mjs` renders the agent-facing handoff page in jsdom with a
stubbed redeem response and checks that the token, base URL, MCP endpoint, and
one-time warning render while the URL fragment is stripped.

`changelog_smoke.mjs` is the post-update notice regression harness. It checks
both dismissal controls, immediate removal, no reappearance on navigation, a
genuinely new release still being shown, and the failed-persistence path. Its
API stub returns fresh objects on every read so a stale `state.status` snapshot
cannot be masked by shared test objects.
