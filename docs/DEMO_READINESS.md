# November 9 demo update

The sponsor lounge contains four clearly labeled fictional booths. Real attendees and their contact data are separate. Each booth explains its sponsor's role in NETwork and supplies a relevant concierge prompt. Synthetic booth profiles obey the existing sponsor-intent, synthetic-data, consent, event, and expiry filters; they cannot receive real introductions.

The API and worker are deployed as `network-concierge:demo-20261109-r1` at the existing [Cloudflare URL](https://kodak-role-congressional-touring.trycloudflare.com). Public health, readiness, QR, sponsor catalog, and authorization checks pass; the served HTML, CSS, and JavaScript hashes match the workspace. Radar is running and ready. See [deployment evidence](evidence/demo-readiness.json). The source changes remain in the workspace; no new Gitar PR review is claimed.

## Backup

Before the schema change or sponsor seeding, `scripts.backup_data` exported all 29 NETwork indices (882 documents) to `backups/20261109T232403Z/`. Each encrypted file was read back and decrypted to verify its record count. `manifest.json` records per-file SHA-256 checksums and completion status. This directory is excluded from Git and Docker contexts.

The export uses the existing `CONTACT_ENCRYPTION_KEY`; retain that key separately. It contains mappings, settings, aliases, document IDs, and document sources, including legacy indices. It is a logical export with a separate scroll snapshot per index, not a transactional cluster snapshot. Inference vectors may need regeneration when restoring semantic fields. Restore into isolated indices first and re-check current consent/expiry before exposing any recovered attendee data. Never blindly restore expired sessions, connections, or withdrawn profiles into the running event.

## Availability behavior

An indexed attendee can highlight only their own card. The button defaults to 15 minutes and supports immediate pause. The list shows only other real, currently discoverable attendees in the same event. Server date filters and policy rechecks exclude expired, withdrawn, synthetic, and sponsor cards. A stale free-text availability value is not treated as live presence. Availability does not imply that a browser tab is still open or that a meeting is booked.

The additive migration is `python -m scripts.enable_availability`. It adds `available_until` to the existing public card mapping without reindexing attendees. The API updates this metadata only, preserving existing semantic fields.

## Demo sequence

1. Open the Cloudflare URL in the attendee's own browser. Join with a reviewed card.
2. Select **I'm available now**. In a second opted-in browser, open **People** and see that attendee under **Open to connect**. Open their card; introductions retain recipient approval.
3. Select **Pause availability**, then refresh the second browser's availability section. The card disappears from that section.
4. Open **Sponsors**. Show Elastic, Radar, Dash0, and Gitar. The booth scenarios are explicitly fictional. Use a booth's concierge prompt to enable sponsor intent and demo profiles.
5. Open **2D / 3D** only when demonstrating the network. Graphs refresh every 8 seconds, render at most 30 fps, and stop when the tab is hidden. Phone 3D rotation is manual.
6. Use **Stack** for the application's telemetry view; keep actual Dash0, Radar, and Gitar evidence available separately. Displaying a sponsor card alone is not proof of an external service action.

## Validation

The local Python suite covers 117 tests, including new availability authorization, expiry, pause, withdrawal, event isolation, synthetic exclusion, Elastic filter parity, and idempotent sponsor seeding. JavaScript syntax and Helm lint checks pass. Browser checks cover mobile joining, availability controls, sponsor cards, and absence of horizontal page overflow at a 390 px viewport. Hardware-level frame timing on a physical phone remains a manual check.

## Rollback

The previous Kubernetes image is `network-concierge:a4d1bf4`. Preserve current Secrets, service selectors, tunnel, and audience. A rollback can restore the preceding deployment revision; the optional date mapping is backward compatible. No attendee data migration is needed.
