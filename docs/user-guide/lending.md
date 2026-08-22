# Lending

Shelf tracks who has what, nags you when it's late, and keeps the history.

## Borrowers

Settings → Library → **Borrowers**: add the people you lend to (name, optional
contact). Deleting a borrower keeps their loan history attached to the items.

## Lend and return

On **Scan**, switch to **Lend**, pick the borrower, optionally set a due date,
and scan each item. Switch to **Return** and scan to check back in. No
scanner handy? The item page has **Lend** / **Check in** buttons too.

## Seeing what's out

- Browse → **Lent out** filter.
- The item page shows borrower and date.
- Overdue items carry a red badge. "Overdue" means past the due date, or —
  for loans with no due date — older than the **Overdue after** threshold
  in Settings → Library → Lending (default 28 days; 0 disables).

## Reminders

Settings → Library → **Lending** → Notification URL. Two formats:

- **ntfy** — a topic URL like `https://ntfy.sh/my-shelf` (self-hosted ntfy
  works too). You'll get a push notification on your phone.
- **Webhook (JSON)** — POSTs `{title, message}` to any URL: Home Assistant,
  Discord/Slack via a relay, n8n.

**Send test** fires one immediately. Once set, a daily digest lists every
overdue loan; it is sent at most once per day and only when something is
overdue. The URL is stored encrypted (an ntfy topic is effectively a
secret).

## History

Each item keeps its loan history (who, when out, when back). Borrower
history is visible from the Borrowers card.
