# WIKTAC Tactical Store (Umbrel community store)

This version pulls a **prebuilt image** from GitHub Container Registry (GHCR).

## One-time click-only steps
1) In GitHub repo: go to **Actions** and click **Enable** (if asked).
2) Wait for workflow **Build & Publish WIKTAC Node Agent** to finish (green check).
3) In GitHub: Packages → ensure `wiktac-node-agent` exists and is **public** (or at least accessible).
4) In Umbrel: Reinstall the app.

Image used by Umbrel:
- `ghcr.io/MAXXIMUXX99/wiktac-node-agent:0.1.2`
