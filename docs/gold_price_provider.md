# Gold price provider configuration

The adapter uses GoldTimo's [documented JSON API](https://goldtimo.com/eg/en/docs).
Its [buy/sell explanation](https://goldtimo.com/eg/en/gold) describes the customer's
perspective. The fixed mapping is `gramSell -> SHOP_BUY`,
`gramBuy -> SHOP_SELL`. A reversed spread is rejected, never swapped.
The API's `unit: oz` describes the separate ounce `price`; the documented
`gramBuy` and `gramSell` fields already contain prices per gram.

Normal operation uses **Settings → إعدادات مصدر أسعار الذهب**. Enter the key,
optionally test it before saving, then save. The key is stored in the current
Windows user's Credential Manager using keyring's Windows backend, service
`masGold`, account `goldtimo_api_key`. It is never returned to QML. The source
URL and timeout use the existing GOLDTIMO source's `notes.connection` JSON;
refresh/staleness and primary selection use existing engine settings. Saving
preserves the current mode, fallback/reference sources and active prices.

Credential precedence is OS credential, then environment fallback, then not
configured. Deleting the local credential does not remove an independently set
developer environment key. Normal employees need no PowerShell setup.
OS-store failures are reported safely; the app never substitutes a plaintext
credential file. DB configuration and the OS store cannot share a transaction:
if key storage fails after configuration commits, the UI explicitly reports that
only connection settings were saved and asks the employee to retry the key.

Optional developer environment or local `.env` fallback (never commit keys):

```dotenv
MASGOLD_GOLDTIMO_BASE_URL=https://goldtimo.com/api/v1
MASGOLD_GOLDTIMO_API_KEY=your-private-key
MASGOLD_GOLDTIMO_TIMEOUT_SECONDS=10
```

The key is sent in the `x-api-key` header. Only HTTPS base URLs are accepted;
redirects are rejected so credentials cannot follow another host. Keys are
excluded from configuration representations and transport error messages.
Timeout must be between 0.1 and 30 seconds. A shared elapsed-time budget covers
the three requests, with a bounded socket timeout on each request and checks
between response chunks. One blocking read can finish after the shared budget
by at most its socket timeout; all work belongs in the background worker.
Each response is limited to 64 KiB. There are no automatic transport retries.

Without a key, `configured_providers()` returns an empty registry. It does not
create or enable a database source, change source selection, or switch mode.
The existing database settings determine refresh, primary/fallback/reference
selection, staleness, and deviation. Source rows must use the code `GOLDTIMO`
for this adapter; credentials are never stored in those rows.

`GoldPriceService._fetch()` resolves the saved connection options and credential
again before each production fetch, so saving/deleting takes effect without a
restart. The UI only accepts GoldTimo's documented HTTPS base URL, preventing
credential forwarding to an arbitrary hostname. Connection tests use the
entered unsaved key when present and run in a background thread. They perform
no quote/activation/audit writes and never change engine mode. All settings
operations, including tests, freshly authorize SETTINGS_MANAGE from AppSession.

The provider fetches 18K, 21K and 24K using three documented endpoints. All three
must be valid and have the same `updatedAt` timestamp. A request that straddles
a provider update is rejected; the next scheduled refresh can retry while the
engine retains its last good set. The API has no documented batch endpoint.
Provider timestamps remain distinct from client fetch times. Repeated provider
timestamps never make a quote younger. The complete set shares one fetch
completion time. Coherence is checked against the exact provider timestamps,
including fractional seconds. Timestamps are then normalized to UTC whole seconds, matching the
existing MySQL `DATETIME` precision so reloading a quote cannot create a false
provider-timestamp change.

The documented free quota is 120 requests per hour. Three requests per set use
that quota at a 90-second refresh interval, without room for retries or other
clients. Configure the existing refresh setting to suit the account's quota;
60-second refreshes exceed the free tier. HTTP 429 is surfaced as a provider
failure, preserving the last good set.

Prices are parsed directly as `Decimal`, validated before publication and
rounded to four decimal places with `ROUND_HALF_UP`, matching the existing
price column precision and price-derivation convention. GoldTimo's public
explanation describes a dealer margin in its customer buy price; the adapter
does not subtract or invent any making charge.

The interface permits another provider to replace GoldTimo by implementing
`source_code` and `fetch() -> tuple[NormalizedQuote, ...]`, without changing
Billing or the service. A future adapter must document its own explicit shop
buy/sell mapping and pass the same complete-set validation.
