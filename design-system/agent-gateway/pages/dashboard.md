# Page Override: Management Workspace (`index.html`, `styles.css`, `app.js`)

## UI UX Pro Max Operational Rules Applied

1. **Zero Secret Persistence in Browser Storage**:
   - `app.js` never accesses `localStorage`, `sessionStorage`, `indexedDB`, or `document.cookie`.
   - Operator preferences (`ui_theme`) and post-update acknowledgements (`seen_version`) are persisted server-side in SQLite via `/api/admin/settings` and `/api/admin/releases/acknowledge`.
2. **Strict Content-Security-Policy Compliance**:
   - `default-src 'self'`; zero external fonts, scripts, or stylesheets.
   - Zero `style="..."` inline attributes or `innerHTML` assignments; all DOM updates use safe `document.createElement` builders (`el()`).
3. **Touch Targets & Density**:
   - Primary buttons and form inputs maintain a `44px` minimum height (`min-height: 44px`).
   - Secondary table action buttons use `data-size="sm"` (`min-height: 36px`) with `8px` minimum gap.
4. **Accessibility & Keyboard Navigation**:
   - Skip link (`<a class="skip-link" href="#main">`), landmark roles (`navigation`, `main`, `region`, `dialog`, `status`, `alert`), and focus restoration when modals close.
   - Global keyboard shortcuts (`g o`, `g c`, `g k`, `g n`, `g t`, `g p`, `g a`, `g l`, `g u`, `g s`, `r`, `?`, `Escape`) disabled when focus is inside form controls.
   - `@media (prefers-reduced-motion: reduce)` collapses all transitions and animations.
