# Daily captures — phone payment notifications into Tally

Daily mode can be *assisted* by the phone: an Android automation reads the payment
notification Google Wallet (or a bank app) shows after a tap, and POSTs it to
Tally as a **pending capture**. Captures are suggestions — they never touch the
ledger until you confirm them in Daily.

> This is a helper for Google-Pay/NFC daily spends only. Everything else stays
> manual. For why a *Google* feed isn't possible (no consumer Wallet API, Takeout
> is on-demand only), see `docs/bank-integration-plan.md`.

## Why the phone and not Google

- Google Wallet has **no consumer transaction API** — the developer API is
  issuer-side (passes/tickets). Google Takeout can export Wallet/Pay data, but
  it's user-triggered and its only recurring option is every 2 months.
- Reading the OS notification needs **no Google credentials and no scraping**: it
  is permission you grant on your own device.
- The same hook also catches **bank app push notifications** (HSBC / Revolut /
  Amex), which give better merchant/amount fidelity than Wallet's own text. Start
  with Wallet; add banks if you want.
- iOS has no equivalent listener — this route is Android-only.

## 1. Server setup

Add a per-user token to the environment (comma-separated, like
`FINANCE_TRACKER_USERS`):

```sh
# .env
DAILY_CAPTURE_TOKENS="ivan:<long-random-token>"
```

The token can only create pending captures for that user — it cannot read the
ledger or spend anything. Generate one with `openssl rand -hex 24`, restart the
service, and keep it out of logs/screenshots.

If `DAILY_CAPTURE_TOKENS` is unset, the ingest route returns **401** for everyone.

## 2. Enable Wallet purchase notifications

Google Wallet app → profile → Wallet settings → turn **purchase notifications**
on. Without this, taps produce no notification to read.

## 3. MacroDroid recipe (fastest path)

MacroDroid is a free Android automation app. Create one macro:

1. **Trigger**: *Notification Received* → choose the **Google Wallet** app
   (add more triggers for your bank apps later).
2. **Action**: *HTTP Request*
   - Method: `POST`
   - URL: `https://<your-tally-host>/api/spending/daily/capture`
   - Header: `Authorization: Bearer <long-random-token>`
   - Header: `Content-Type: application/json`
   - Body (JSON): use MacroDroid's magic text for the notification title/text:

     ```json
     {
       "amount": "[notification_text]",
       "title": "[notification_text]",
       "source": "wallet",
       "device": "[device_name]",
       "captured_at": "[current_time]"
     }
     ```

Real notifications are not valid JSON values, so extract with **regex** first
(MacroDroid *Set Variable → Regular expression*), then reference the captured
groups in the body. For a Wallet notification like
`You paid £12.34 at TESCO STORES with your Visa •••• 1234`:

- amount: pattern `£([0-9]+\.[0-9]{2})` → `$1`
- merchant: pattern `at (.+?) with` → `$1`

Body becomes:

```json
{
  "amount": "{amount}",
  "title": "{merchant}",
  "source": "wallet",
  "device": "pixel",
  "captured_at": "{current_time}"
}
```

Tasker works the same way: *Profile → Event → UI → Notification* (or *AutoInput*),
then *Task → Net → HTTP Request*, with *Variable Search Replace* for the regex.

Test the endpoint before wiring the phone:

```sh
curl -sS -X POST https://<host>/api/spending/daily/capture \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"capture_id":"test-1","amount":12.34,"title":"TESCO STORES","source":"wallet"}'
```

## API contract

`POST /api/spending/daily/capture` — bearer-token auth (`Authorization: Bearer …`
or `X-Capture-Token`), rate-limited to 60 requests/min/user.

| Field | Required | Notes |
|---|---|---|
| `amount` | yes | positive number |
| `title` | yes | merchant; `merchant`/`description` accepted as aliases |
| `capture_id` | no | stable id from the phone; makes retries idempotent (generated if omitted) |
| `date` | no | `YYYY-MM-DD`; defaults to today, future values are clamped to today |
| `source` | no | `wallet` \| `bank` \| `android` \| `other` (default `wallet`) |
| `captured_at` | no | display only |
| `card_last4` | no | display only |
| `device` | no | display only |
| `merchant_raw` | no | original notification text, kept for debugging |

Response: `{"ok": true, "duplicate": <bool>, "capture": {…}}`. A `duplicate:true`
means the same capture was already recorded (same `capture_id`, or same
date+amount+title still pending) — safe to ignore.

Confirming/dismissing happens through the normal signed-in UI:

- Confirm: `POST /api/spending/daily/entry` with `capture_id` — creates the
  manual spend (or **links** an existing manual of the same amount within ±3
  days, so nothing is double-counted) and marks the capture accepted.
- Dismiss: `POST /api/spending/daily/capture/<id>/dismiss`.

## In the UI

Pending captures appear as a **Captured from your phone** panel above the entry
form on Daily. Tapping one fills the amount/title/date; you pick a category and
press **Add spend**. `×` ignores it. The panel is hidden when nothing is pending.

## Reliability — treat it as a helper

- Battery optimisation can kill the listener; notifications are missed while the
  phone is off. Manual entry stays the fallback and the statement reconcile still
  corrects auth-vs-settled amounts later.
- Notification wording varies by app/locale/version — the regex may need tuning.
  Unparsed notifications are still kept as `merchant_raw` so nothing is silently
  lost.
- Wallet shows the **authorisation** amount; tips/delayed settlement may differ.
  That's fine — captures are pre-fills, not the record of truth.
- A capture is only ever a suggestion: no capture is written to the ledger
  without an explicit confirm in Daily.
