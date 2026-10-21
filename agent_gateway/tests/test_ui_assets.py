"""Static UI checks for the design-system rules the panel claims to follow.

These are static checks, not a rendered browser audit. They exist so a future
change cannot silently remove the accessible focus ring, the reduced-motion
handling, the SVG icon set, or drop a colour pair below WCAG AA contrast.

All assertions are read-only: nothing here renders HTML or executes JavaScript.
"""

from __future__ import annotations

import re
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[1] / "src" / "gateway" / "ui" / "static"
CSS = (UI_DIR / "styles.css").read_text(encoding="utf-8")
HTML = (UI_DIR / "index.html").read_text(encoding="utf-8")
JS = (UI_DIR / "app.js").read_text(encoding="utf-8")

MIN_CONTRAST_TEXT = 4.5
MIN_CONTRAST_UI = 3.0


def luminance(hex_colour: str) -> float:
    raw = hex_colour.lstrip("#")
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def theme_tokens(selector: str) -> dict[str, str]:
    """Return the custom property values declared inside one selector block."""

    start = CSS.index(selector)
    block = CSS[start : CSS.index("}", start)]
    return dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block))


# The default :root block holds the light theme; the media query overrides it.
LIGHT = theme_tokens(":root {")
DARK = theme_tokens("@media (prefers-color-scheme: dark) {\n  :root {")
THEMES = {"light": LIGHT, "dark": DARK}


def test_both_themes_define_the_same_tokens() -> None:
    assert LIGHT.keys() == DARK.keys()
    for name in ("--color-foreground", "--color-muted-foreground", "--color-card", "--color-border"):
        assert name in LIGHT


def test_text_colours_meet_aa_contrast_on_surfaces() -> None:
    for theme_name, theme in THEMES.items():
        text_tokens = (
            "--color-foreground",
            "--color-muted-foreground",
            "--color-danger",
            "--color-warning",
            "--color-info",
        )
        for name in text_tokens:
            for surface in ("--color-card", "--color-card-2", "--color-background"):
                ratio = contrast(theme[name], theme[surface])
                assert ratio >= MIN_CONTRAST_TEXT, (theme_name, name, surface, round(ratio, 2))


def test_solid_button_pairs_meet_aa_contrast() -> None:
    for theme_name, theme in THEMES.items():
        accent = contrast(theme["--color-on-accent"], theme["--color-accent-strong"])
        danger = contrast(theme["--color-on-danger"], theme["--color-danger"])
        assert accent >= MIN_CONTRAST_TEXT, (theme_name, round(accent, 2))
        assert danger >= MIN_CONTRAST_TEXT, (theme_name, round(danger, 2))


def test_borders_are_visible_but_subtle() -> None:
    for theme_name, theme in THEMES.items():
        # Non-text UI boundaries need 3:1 against the adjacent surface.
        assert contrast(theme["--color-border"], theme["--color-card"]) >= MIN_CONTRAST_UI, theme_name
        # Decorative separators are allowed below 3:1, but only for non-essential borders.
        assert contrast(theme["--color-border-soft"], theme["--color-card"]) < MIN_CONTRAST_UI, theme_name


def test_interactive_targets_are_at_least_44_pixels() -> None:
    assert re.search(r"--tap:\s*44px", CSS)
    assert CSS.count("min-height: var(--tap)") >= 3  # buttons, form controls, nav links
    for block in (".button {", ".nav-link {"):
        start = CSS.index(block)
        assert "min-height: var(--tap)" in CSS[start : CSS.index("}", start)], block


def test_compact_controls_are_limited_to_secondary_actions() -> None:
    # 36px is allowed only for the small button variant, never for primary
    # navigation or form inputs.
    small = CSS[CSS.index('.button[data-size="sm"] {') :]
    small_block = small[: small.index("}")]
    assert "min-height: 36px" in small_block
    assert "min-height: 36px" not in CSS[: CSS.index('.button[data-size="sm"] {')]


def test_focus_ring_is_never_suppressed_globally() -> None:
    assert ":focus-visible" in CSS
    # `outline: none` is only allowed for non-keyboard focus.
    for match in re.finditer(r"outline:\s*none", CSS):
        context = CSS[max(0, match.start() - 120) : match.start()]
        assert "not(:focus-visible)" in context, context


def test_focus_visibility_rule_exists_for_keyboard_users() -> None:
    marker = ":where(a, button, input, select, textarea, summary, [tabindex]):focus-visible"
    assert marker in CSS
    block = CSS[CSS.index(marker) :]
    assert "outline: 2px solid var(--color-ring)" in block[:200]


def test_reduced_motion_is_respected() -> None:
    marker = "@media (prefers-reduced-motion: reduce)"
    assert marker in CSS
    block = CSS[CSS.index(marker) :]
    assert "animation-duration: 0.001ms !important" in block[:600]
    assert "transition-duration: 0.001ms !important" in block[:600]


def test_no_emoji_or_raster_icons_are_used() -> None:
    emoji = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf]")
    for name, content in (("index.html", HTML), ("app.js", JS), ("styles.css", CSS)):
        assert not emoji.search(content), f"emoji found in {name}"
    assert "<svg" in HTML
    assert "ICONS = {" in JS
    assert 'aria-hidden="true"' in HTML


def test_assets_are_relative_and_offline_only() -> None:
    assert 'href="static/styles.css"' in HTML
    assert 'src="static/app.js"' in HTML
    assert "http://" not in HTML and "https://" not in HTML
    assert "fonts.googleapis.com" not in CSS
    assert "@import" not in CSS
    assert "@font-face" not in CSS
    # No network calls other than the same-origin admin API.
    for call in re.findall(r"fetch\(([^)]{0,80})", JS):
        assert "api/admin/" in call or "url" in call, call


def test_ui_uses_no_persistent_browser_storage_for_secrets() -> None:
    for api in ("localStorage.", "sessionStorage.", "indexedDB.", "document.cookie", "setItem("):
        assert api not in JS, api
    # Tokens are rendered once and copied, never persisted.
    assert "token-box" in JS
    assert "shown once" in JS.lower()
    assert "clipboard" in JS


def test_tables_expose_sorting_state_and_accessible_names() -> None:
    assert "aria-sort" in JS
    assert 'role="region"' in JS
    assert "<caption>" in JS
    assert 'scope="col"' in JS


def test_errors_are_linked_to_fields_and_announced() -> None:
    assert "error-summary" in JS
    assert 'setAttribute("role", "alert")' in JS
    assert "aria-invalid" in JS
    assert "field-error" in JS
    assert "scroll-padding-top" in CSS
    assert 'class="skip-link"' in JS
    assert 'aria-live="polite"' in HTML
    assert "aria-describedby" in JS


def test_modals_are_dialogs_with_escape_and_focus_return() -> None:
    assert 'role="dialog"' in HTML
    assert 'aria-modal="true"' in HTML
    assert "trapFocus" in JS
    assert 'event.key === "Escape"' in JS
    assert "lastFocused" in JS


def test_loading_empty_and_error_states_are_explicit() -> None:
    assert "emptyState(" in JS
    assert "skeleton(" in JS
    assert "aria-busy" in JS
    assert "announce(" in JS


def test_only_one_h1_and_a_main_landmark_per_view() -> None:
    assert HTML.count("<h1") <= 1
    assert 'id="main-content"' in HTML
    assert 'aria-label="Gateway sections"' in JS


def test_status_is_never_conveyed_by_colour_alone() -> None:
    assert "statusBadge" in JS
    assert "badge(" in JS
    # Every badge carries a text label next to the tone.
    badge_body = JS[JS.index("function badge(") : JS.index("function statusBadge(")]
    assert "esc(text)" in badge_body


def test_markup_avoids_inline_styles_blocked_by_csp() -> None:
    # The panel is served with `style-src 'self'`; inline style attributes would
    # be silently dropped by the browser, so spacing uses utility classes.
    assert 'style="' not in HTML
    assert 'style="' not in JS
    assert "element.style" in JS or ".style." in JS  # CSSOM writes are allowed
    assert ".mt-4" in CSS and ".nowrap" in CSS


def test_viewport_allows_zoom() -> None:
    assert "user-scalable=no" not in HTML
    assert "maximum-scale" not in HTML
