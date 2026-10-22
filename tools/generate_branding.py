"""Generate vector and raster branding assets for Home Assistant Agent Gateway.

Assets produced:
- agent_gateway/src/gateway/ui/static/logo.svg     (64x64 vector brand mark)
- agent_gateway/src/gateway/ui/static/favicon.svg  (32x32 vector favicon)
- agent_gateway/icon.png                           (128x128 Home Assistant App Store icon)
- agent_gateway/logo.png                           (250x100 Home Assistant App Store logo)
- docs/assets/logo.svg                             (128x128 standalone vector emblem)
- docs/assets/banner.svg                           (960x220 README hero banner)
- docs/assets/architecture.svg                     (960x360 security boundary diagram)
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "agent_gateway" / "src" / "gateway" / "ui" / "static"
ADDON_DIR = ROOT / "agent_gateway"
DOCS_ASSETS_DIR = ROOT / "docs" / "assets"

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128" role="img" aria-label="Home Assistant Agent Gateway">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0B192C"/>
      <stop offset="55%" stop-color="#0F2942"/>
      <stop offset="100%" stop-color="#091526"/>
    </linearGradient>
    <linearGradient id="shieldGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#0284C7"/>
    </linearGradient>
    <linearGradient id="coreGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#4ADE80"/>
      <stop offset="100%" stop-color="#16A34A"/>
    </linearGradient>
  </defs>
  <!-- Outer rounded container that contrasts cleanly on both light and dark HA backgrounds -->
  <rect x="6" y="6" width="116" height="116" rx="26" fill="url(#bgGrad)" stroke="#38BDF8" stroke-width="3" stroke-opacity="0.65"/>
  <rect x="12" y="12" width="104" height="104" rx="21" fill="none" stroke="#1E3A5F" stroke-width="1.5"/>

  <!-- Subtle circuit/conduit lines representing the 3 guarded connectors (HA, Proxmox, SSH) -->
  <g stroke="#38BDF8" stroke-width="2.5" stroke-linecap="round" opacity="0.55">
    <line x1="64" y1="18" x2="64" y2="28"/>
    <line x1="23" y1="44" x2="33" y2="50"/>
    <line x1="105" y1="44" x2="95" y2="50"/>
    <line x1="28" y1="92" x2="38" y2="84"/>
    <line x1="100" y1="92" x2="90" y2="84"/>
  </g>
  <circle cx="64" cy="18" r="3.5" fill="#38BDF8"/>
  <circle cx="23" cy="44" r="3.5" fill="#38BDF8"/>
  <circle cx="105" cy="44" r="3.5" fill="#38BDF8"/>
  <circle cx="28" cy="92" r="3.5" fill="#4ADE80"/>
  <circle cx="100" cy="92" r="3.5" fill="#4ADE80"/>

  <!-- Outer Policy Shield -->
  <path d="M64 25 L98 39 V65 C98 86 83.5 101.5 64 108 C44.5 101.5 30 86 30 65 V39 Z"
        fill="#0B1324" stroke="url(#shieldGrad)" stroke-width="4.5" stroke-linejoin="round"/>

  <!-- Inner Gateway Arch / Vault Ring -->
  <path d="M64 35 L88 45 V64 C88 79.5 77.5 91.5 64 97 C50.5 91.5 40 79.5 40 64 V45 Z"
        fill="#112239" stroke="#1E3A5F" stroke-width="2" stroke-linejoin="round"/>

  <!-- Central Lock & Policy Gate Node -->
  <rect x="51" y="59" width="26" height="22" rx="5" fill="url(#shieldGrad)"/>
  <path d="M56 59 V52 C56 47.5 59.6 44 64 44 C68.4 44 72 47.5 72 52 V59"
        fill="none" stroke="#BAE6FD" stroke-width="4" stroke-linecap="round"/>
  <circle cx="64" cy="68" r="3.2" fill="#081120"/>
  <path d="M64 71 V76" stroke="#081120" stroke-width="3" stroke-linecap="round"/>

  <!-- Verified Approval Check Badge (bottom-right of shield) -->
  <circle cx="85" cy="85" r="12" fill="url(#coreGrad)" stroke="#0B192C" stroke-width="3"/>
  <path d="M80 85.2 L83.5 88.8 L90.5 81.5" fill="none" stroke="#06140B" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="32" height="32">
  <rect x="6" y="6" width="116" height="116" rx="26" fill="#0B192C" stroke="#38BDF8" stroke-width="5"/>
  <path d="M64 24 L98 38 V65 C98 86 83.5 101.5 64 108 C44.5 101.5 30 86 30 65 V38 Z"
        fill="#112239" stroke="#38BDF8" stroke-width="5.5" stroke-linejoin="round"/>
  <rect x="50" y="58" width="28" height="23" rx="5" fill="#38BDF8"/>
  <path d="M56 58 V51 C56 46.5 59.6 43 64 43 C68.4 43 72 46.5 72 51 V58"
        fill="none" stroke="#E0F2FE" stroke-width="5" stroke-linecap="round"/>
  <circle cx="85" cy="85" r="13" fill="#4ADE80" stroke="#0B192C" stroke-width="3.5"/>
  <path d="M79.5 85 L83.5 89 L91 81" fill="none" stroke="#052E16" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

ADDON_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 200" width="500" height="200" role="img" aria-label="Home Assistant Agent Gateway Logo">
  <defs>
    <linearGradient id="cardGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0B192C"/>
      <stop offset="100%" stop-color="#0F172A"/>
    </linearGradient>
    <linearGradient id="shieldGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#0284C7"/>
    </linearGradient>
  </defs>
  <!-- Self-contained pill card so the logo is legible on both light and dark HA Supervisor themes -->
  <rect x="8" y="14" width="484" height="172" rx="28" fill="url(#cardGrad)" stroke="#38BDF8" stroke-width="3" stroke-opacity="0.65"/>
  <rect x="15" y="21" width="470" height="158" rx="22" fill="none" stroke="#1E293B" stroke-width="1.5"/>

  <!-- Left Shield Emblem -->
  <g transform="translate(30, 36)">
    <path d="M64 14 L104 30 V62 C104 87 86.5 105.5 64 114 C41.5 105.5 24 87 24 62 V30 Z"
          fill="#0B1324" stroke="url(#shieldGrad)" stroke-width="5" stroke-linejoin="round"/>
    <path d="M64 26 L92 38 V61 C92 79 79.5 93 64 100 C48.5 93 36 79 36 61 V38 Z"
          fill="#112239" stroke="#1E3A5F" stroke-width="2" stroke-linejoin="round"/>
    <rect x="49" y="54" width="30" height="25" rx="6" fill="url(#shieldGrad)"/>
    <path d="M55 54 V46 C55 41 59 37 64 37 C69 37 73 41 73 46 V54"
          fill="none" stroke="#BAE6FD" stroke-width="4.5" stroke-linecap="round"/>
    <circle cx="64" cy="64" r="3.6" fill="#081120"/>
    <path d="M64 67.5 V73" stroke="#081120" stroke-width="3.2" stroke-linecap="round"/>
    <circle cx="88" cy="86" r="14" fill="#4ADE80" stroke="#0B192C" stroke-width="3.5"/>
    <path d="M82 86 L86.2 90.2 L94.5 81.8" fill="none" stroke="#052E16" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
  </g>

  <!-- Right Wordmark & Security Tagline -->
  <text x="166" y="74" fill="#38BDF8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="14" font-weight="700" letter-spacing="1.8">HOME ASSISTANT ADD-ON</text>
  <text x="166" y="114" fill="#F8FAFC" font-family="DejaVu Sans, system-ui, sans-serif" font-size="33" font-weight="700">Agent Gateway</text>
  <text x="166" y="144" fill="#94A3B8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="15" font-weight="500">Policy · Approvals · AES-256 Vault · MCP</text>
</svg>
"""

BANNER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 220" width="960" height="220" role="img" aria-label="Home Assistant Agent Gateway Banner">
  <defs>
    <linearGradient id="heroBg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#091322"/>
      <stop offset="50%" stop-color="#0F223A"/>
      <stop offset="100%" stop-color="#0A1628"/>
    </linearGradient>
    <linearGradient id="heroShield" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#0284C7"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="956" height="216" rx="20" fill="url(#heroBg)" stroke="#1E3A5F" stroke-width="2"/>
  <!-- Grid accent -->
  <g stroke="#1E293B" stroke-width="1" opacity="0.55">
    <line x1="0" y1="55" x2="960" y2="55"/>
    <line x1="0" y1="110" x2="960" y2="110"/>
    <line x1="0" y1="165" x2="960" y2="165"/>
    <line x1="220" y1="0" x2="220" y2="220"/>
    <line x1="680" y1="0" x2="680" y2="220"/>
  </g>

  <!-- Brand Emblem -->
  <g transform="translate(46, 38)">
    <rect x="6" y="6" width="132" height="132" rx="28" fill="#0B192C" stroke="#38BDF8" stroke-width="3" stroke-opacity="0.7"/>
    <path d="M72 26 L112 42 V74 C112 99 94.5 117.5 72 126 C49.5 117.5 32 99 32 74 V42 Z"
          fill="#0B1324" stroke="url(#heroShield)" stroke-width="4.5" stroke-linejoin="round"/>
    <rect x="57" y="66" width="30" height="25" rx="6" fill="url(#heroShield)"/>
    <path d="M63 66 V58 C63 53 67 49 72 49 C77 49 81 53 81 58 V66"
          fill="none" stroke="#BAE6FD" stroke-width="4.5" stroke-linecap="round"/>
    <circle cx="72" cy="76" r="3.5" fill="#081120"/>
    <path d="M72 79.5 V85" stroke="#081120" stroke-width="3" stroke-linecap="round"/>
    <circle cx="96" cy="98" r="14" fill="#4ADE80" stroke="#0B192C" stroke-width="3.5"/>
    <path d="M90 98 L94.2 102.2 L102.5 93.8" fill="none" stroke="#052E16" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
  </g>

  <!-- Title and Subtitle -->
  <text x="210" y="60" fill="#38BDF8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="12" font-weight="700" letter-spacing="1.4">HOME ASSISTANT OS APP / ADD-ON · SECURITY BOUNDARY FOR AI AGENTS</text>
  <text x="210" y="104" fill="#F8FAFC" font-family="DejaVu Sans, system-ui, sans-serif" font-size="33" font-weight="700">Home Assistant Agent Gateway</text>
  <text x="210" y="136" fill="#94A3B8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="14">Deny-by-default policies, single-use human approvals, AES-256-GCM vault, and MCP HTTP.</text>

  <!-- Capability Pills -->
  <g transform="translate(210, 158)" font-family="DejaVu Sans, system-ui, sans-serif" font-size="11.5" font-weight="600">
    <rect x="0" y="0" width="136" height="30" rx="15" fill="#0F2942" stroke="#0284C7" stroke-width="1.5"/>
    <text x="68" y="19.5" fill="#E0F2FE" text-anchor="middle">Ingress UI :8099</text>

    <rect x="148" y="0" width="166" height="30" rx="15" fill="#0F2942" stroke="#0284C7" stroke-width="1.5"/>
    <text x="231" y="19.5" fill="#E0F2FE" text-anchor="middle">MCP + REST API :8098</text>

    <rect x="326" y="0" width="188" height="30" rx="15" fill="#0B2B22" stroke="#16A34A" stroke-width="1.5"/>
    <text x="420" y="19.5" fill="#DCFCE7" text-anchor="middle">AES-256-GCM + Argon2id</text>

    <rect x="526" y="0" width="194" height="30" rx="15" fill="#1E293B" stroke="#475569" stroke-width="1.5"/>
    <text x="623" y="19.5" fill="#F1F5F9" text-anchor="middle">HA · Proxmox RO · Safe SSH</text>
  </g>
</svg>
"""

ARCHITECTURE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 340" width="960" height="340" role="img" aria-label="Home Assistant Agent Gateway Architecture">
  <rect x="2" y="2" width="956" height="336" rx="16" fill="#0B1120" stroke="#1E293B" stroke-width="2"/>
  <text x="32" y="38" fill="#F8FAFC" font-family="DejaVu Sans, system-ui, sans-serif" font-size="18" font-weight="700">Security Boundary &amp; Two-Listener Architecture</text>
  <text x="32" y="60" fill="#94A3B8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="13">External agents never receive upstream tokens or SSH keys; every call traverses scope, policy, approval, and redaction gates.</text>

  <!-- Left: External Agents & Admin -->
  <rect x="32" y="84" width="230" height="106" rx="12" fill="#0F172A" stroke="#38BDF8" stroke-width="1.5"/>
  <text x="48" y="110" fill="#38BDF8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="12" font-weight="700">EXTERNAL AI AGENTS (LAN / VPN)</text>
  <text x="48" y="132" fill="#F8FAFC" font-family="DejaVu Sans, system-ui, sans-serif" font-size="14" font-weight="600">Claude / Cursor / Windows</text>
  <text x="48" y="154" fill="#94A3B8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="12">POST /mcp · /api/v1/* (:8098)</text>
  <text x="48" y="174" fill="#64748B" font-family="DejaVu Sans, system-ui, sans-serif" font-size="11.5">Auth: Bearer hag_... (SHA-256)</text>

  <rect x="32" y="206" width="230" height="106" rx="12" fill="#0F172A" stroke="#4ADE80" stroke-width="1.5"/>
  <text x="48" y="232" fill="#4ADE80" font-family="DejaVu Sans, system-ui, sans-serif" font-size="12" font-weight="700">HOME ASSISTANT INGRESS</text>
  <text x="48" y="254" fill="#F8FAFC" font-family="DejaVu Sans, system-ui, sans-serif" font-size="14" font-weight="600">Admin Control Panel (:8099)</text>
  <text x="48" y="276" fill="#94A3B8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="12">Supervisor Peer 172.30.32.2</text>
  <text x="48" y="296" fill="#64748B" font-family="DejaVu Sans, system-ui, sans-serif" font-size="11.5">Vault · Policies · Approvals · Audit</text>

  <!-- Arrows into Gateway -->
  <g stroke="#38BDF8" stroke-width="2" fill="none">
    <line x1="262" y1="137" x2="322" y2="137"/>
    <polygon points="322,133 330,137 322,141" fill="#38BDF8" stroke="none"/>
  </g>
  <g stroke="#4ADE80" stroke-width="2" fill="none">
    <line x1="262" y1="259" x2="322" y2="259"/>
    <polygon points="322,255 330,259 322,263" fill="#4ADE80" stroke="none"/>
  </g>

  <!-- Center: Gateway Core -->
  <rect x="328" y="84" width="312" height="228" rx="14" fill="#111C33" stroke="#0284C7" stroke-width="2"/>
  <text x="346" y="112" fill="#E0F2FE" font-family="DejaVu Sans, system-ui, sans-serif" font-size="14" font-weight="700">Agent Gateway Enforcement Core</text>

  <rect x="344" y="126" width="280" height="36" rx="8" fill="#0B1324" stroke="#1E3A5F"/>
  <text x="356" y="149" fill="#F8FAFC" font-family="DejaVu Sans, system-ui, sans-serif" font-size="11.5">1. Scope &amp; Deny-by-Default Policy Engine</text>

  <rect x="344" y="170" width="280" height="36" rx="8" fill="#0B1324" stroke="#1E3A5F"/>
  <text x="356" y="193" fill="#F8FAFC" font-family="DejaVu Sans, system-ui, sans-serif" font-size="11.5">2. Single-Use Parameter-Bound Approvals</text>

  <rect x="344" y="214" width="280" height="36" rx="8" fill="#0B1324" stroke="#1E3A5F"/>
  <text x="356" y="237" fill="#F8FAFC" font-family="DejaVu Sans, system-ui, sans-serif" font-size="11.5">3. AES-256-GCM Credential Vault (Argon2id)</text>

  <rect x="344" y="258" width="280" height="36" rx="8" fill="#0B1324" stroke="#1E3A5F"/>
  <text x="356" y="281" fill="#F8FAFC" font-family="DejaVu Sans, system-ui, sans-serif" font-size="11.5">4. Redacting SQLite WAL Audit Trail</text>

  <!-- Arrows to Targets -->
  <g stroke="#64748B" stroke-width="2" fill="none">
    <line x1="640" y1="120" x2="694" y2="120"/>
    <polygon points="694,116 702,120 694,124" fill="#64748B" stroke="none"/>
    <line x1="640" y1="198" x2="694" y2="198"/>
    <polygon points="694,194 702,198 694,202" fill="#64748B" stroke="none"/>
    <line x1="640" y1="276" x2="694" y2="276"/>
    <polygon points="694,272 702,276 694,280" fill="#64748B" stroke="none"/>
  </g>

  <!-- Right: Upstream Targets -->
  <rect x="706" y="84" width="222" height="66" rx="10" fill="#0F172A" stroke="#334155" stroke-width="1.5"/>
  <text x="722" y="109" fill="#38BDF8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="13" font-weight="700">Home Assistant Core</text>
  <text x="722" y="131" fill="#94A3B8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="11.5">State read · Approval-gated services</text>

  <rect x="706" y="165" width="222" height="66" rx="10" fill="#0F172A" stroke="#334155" stroke-width="1.5"/>
  <text x="722" y="190" fill="#F59E0B" font-family="DejaVu Sans, system-ui, sans-serif" font-size="13" font-weight="700">Proxmox VE (Read-Only)</text>
  <text x="722" y="212" fill="#94A3B8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="11.5">Nodes · VMs · Containers · Tasks</text>

  <rect x="706" y="246" width="222" height="66" rx="10" fill="#0F172A" stroke="#334155" stroke-width="1.5"/>
  <text x="722" y="271" fill="#4ADE80" font-family="DejaVu Sans, system-ui, sans-serif" font-size="13" font-weight="700">Restricted SSH Targets</text>
  <text x="722" y="293" fill="#94A3B8" font-family="DejaVu Sans, system-ui, sans-serif" font-size="11.5">known_hosts · Non-root · Allowlists</text>
</svg>
"""


def render_png(svg_text: str, out_path: Path, width: int, height: int) -> None:
    raw_png = resvg_py.svg_to_bytes(
        svg_string=svg_text,
        width=width * 2,
        height=height * 2,
        font_dirs=["/usr/share/fonts/truetype/dejavu"],
        sans_serif_family="DejaVu Sans",
        monospace_family="DejaVu Sans Mono",
    )
    img = Image.open(BytesIO(raw_png)).convert("RGBA")
    resized = img.resize((width, height), Image.Resampling.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    resized.save(out_path, format="PNG", optimize=True)


def main() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    (STATIC_DIR / "logo.svg").write_text(ICON_SVG.strip() + "\n", encoding="utf-8")
    (STATIC_DIR / "favicon.svg").write_text(FAVICON_SVG.strip() + "\n", encoding="utf-8")
    (DOCS_ASSETS_DIR / "logo.svg").write_text(ICON_SVG.strip() + "\n", encoding="utf-8")
    (DOCS_ASSETS_DIR / "banner.svg").write_text(BANNER_SVG.strip() + "\n", encoding="utf-8")
    (DOCS_ASSETS_DIR / "architecture.svg").write_text(ARCHITECTURE_SVG.strip() + "\n", encoding="utf-8")

    render_png(ICON_SVG, ADDON_DIR / "icon.png", 128, 128)
    render_png(ADDON_LOGO_SVG, ADDON_DIR / "logo.png", 250, 100)
    render_png(BANNER_SVG, DOCS_ASSETS_DIR / "banner.png", 960, 220)
    render_png(ARCHITECTURE_SVG, DOCS_ASSETS_DIR / "architecture.png", 960, 340)


if __name__ == "__main__":
    main()
