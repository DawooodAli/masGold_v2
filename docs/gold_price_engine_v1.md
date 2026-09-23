# Gold Price Engine V1

The engine reuses the live `gold_price_sources`, `gold_price_quotes`,
`gold_price_activations`, `active_gold_prices`, `gold_price_engine_settings`,
`karats`, and `audit_log` tables. Live schema, foreign keys and initial settings
were inspected before implementation. No migration is needed. No schema/data
changes were executed during development, and no commit was created.

At inspection the engine was `MANUAL`, with no quotes or active prices and no
primary/fallback/reference source selected. The seeded sources were `MANUAL`
and disabled `LEGACY_PRODUCTION`. Therefore the initial UI correctly reports
unavailable until an authorized user activates manual prices, or an automatic
provider is configured and the user explicitly returns to AUTO.

## File → class/component → method

| File | Class/component | Methods / responsibility |
| --- | --- | --- |
| `services/gold_price_provider.py` | `NormalizedQuote`, `GoldPriceProvider`, `GoldTimoProvider` | `fetch()`, `validate_quotes()`, `configured_providers()`; replaceable documented adapters and complete-set validation |
| `config/gold_price_config.py` | `GoldTimoConfig` | `from_env()`; URL, private API key, bounded timeout |
| `repositories/gold_price_repository.py` | `GoldPriceRepository` | `get_settings()`, `get_sources()`, `get_karats()`, `get_active()`, `latest_source_quotes()`, `append_publication()`, `set_mode()`, `append_rejection()` |
| same repository file | `gold_price_transaction()` | dedicated bounded MySQL connection, UTC, READ COMMITTED, commit/rollback |
| `services/gold_price_service.py` | `GoldPriceService` | `snapshot()`, `refresh()`, `preview_manual()`, `activate_manual()`, `return_to_auto()` |
| same service file | `GoldPriceService`, `BillingPriceSet`, `BillingKaratPrice` | `get_billing_price_set()`, `lock_and_validate_for_finalization()`; exact Decimal prices and publication identity |
| `app/workers/gold_price_worker.py` | `GoldPriceWorker` | `run()`; price I/O outside GUI thread, request bound to its initiating session |
| `app/viewmodels/gold_price_viewmodel.py` | `GoldPriceViewModel` | `refresh()`, `previewManual()`, `confirmManual()`, `returnToAuto()`, `_session_changed()`, `shutdown()` |
| `main.py` | `ApplicationController` | `__init__()`, `_open_operational()`; real session injection and persistent worker ownership |
| `qml/Main.qml` | `goldPriceArea`, `goldPriceDialog`, `goldAutoConfirmation` | live top-bar status, all-karat table, manual preview/confirmation, explicit AUTO confirmation |
| `tests/test_gold_price_provider.py` | provider/configuration tests | semantic mapping, validation, bounded transport and failures |
| `tests/test_gold_price_repository.py` | repository tests | transactional publication, rollback, append-only audit and provenance |
| `tests/test_gold_price_service.py` | service tests | freshness, fallback/reference/deviation, concurrency, authorization and Billing contract |
| `tests/test_gold_price_qml.py` | worker/panel tests | offscreen QML loading, preview/confirm, GUI heartbeat, no overlap and session lifecycle |
| `tests/test_authentication_qml.py` | existing authentication tests | inject an inert price service so existing lifecycle tests never contact the live database |

## Publication and security

- Network fetching finishes before the activation transaction opens.
- The settings singleton is locked before mode checks and publication. All
  three quotes and activations are appended; all three active pointers change
  in the same transaction. No history row is rewritten.
- The entire set has one source, quote group, provider timestamp and fetch
  time. Every amount is `Decimal` internally and a decimal string in QML.
- Manual entry derives `price(K) = price(21) * K / 21` using four decimal
  places and `ROUND_HALF_UP`, following the existing application's convention.
  The repository consumes normalized per-karat quotes, so future explicit
  per-karat entry can reuse the publication path.
- Protected actions revalidate the active user, employee relationship,
  `APP_LOGIN`, and `SETTINGS_MANAGE` through `AuthorizationService` inside the
  publication transaction. The initiating `ActorContext` comes from the real
  `AppSession`; QML submits prices/reason only.
- Audit metadata preserves the fresh user/optional employee, reason,
  derivation, currency/unit, source, group and quote/activation identities.
- Automatic activation rechecks mode/settings and cannot overwrite MANUAL.
  Returning to AUTO requires an explicit action, fresh authorization, and a
  complete valid automatic set. Failure leaves MANUAL and its prices intact.
- No primary provider credentials/source configuration are seeded by the app.
  Follow [provider configuration](gold_price_provider.md), configure an enabled
  `LOCAL_MARKET` source with code `GOLDTIMO` and select it through the existing
  settings under your administrative configuration process. Do not set mode
  by SQL; use the authorized dialog action. The existing 60-second interval
  exceeds the documented GoldTimo free quota; choose a suitable interval
  (for example 120 seconds for one client) and a stale threshold at least as
  long. Multiple application instances share publication locking, but each
  instance can fetch, so size the provider quota accordingly.
- Fallback and reference use the same provider interface and the configured
  source IDs. No undocumented second provider or scraping adapter is supplied.
  A configured but unavailable reference source prevents publication.
- Maximum deviation applies to both buy and sell prices against the last
  coherent active set and the configured reference. A manual override remains
  possible with authorization and a reason.

## Freshness and the future Billing contract

`FRESH` means a coherent accepted set is within the configured provider-age
threshold. `STALE_ALLOWED` retains an old set and requires acknowledgement.
`UNAVAILABLE` has no coherent accepted set. `ERROR` reports a failed refresh
while preserving the last good set when one exists. Manual prices age from
their activation timestamp and can also become `STALE_ALLOWED`.

Age uses the original provider timestamp, never the latest fetch time.
Receiving unchanged prices with the same timestamp does not insert another
publication, alter its fetch/activation time, or change its token. Older
timestamps or changed prices with the same timestamp are rejected and audited;
a valid configured fallback can still be used. Invalid responses are recorded
as `GOLD_PRICE_REJECTED` audit events without storing nonfinite amounts in
positive-only quote columns or exposing credentials.

All new price timestamps are persisted as UTC whole seconds, matching the
existing MySQL DATETIME precision. Exact provider timestamps are compared for
coherence before truncation. The panel displays ISO timestamps with `+00:00`.

`get_billing_price_set([karat_id, ...])` returns immutable `BillingPriceSet`:

- `price_token`, `mode`, `status`, `warnings`, `requires_acknowledgement`
- `prices`: map keyed by real integer karat IDs, each containing exact Decimal
  buy/sell values and quote/activation IDs
- `provenance`: source, quote group, currency/unit, provider/fetch/activation
  timestamps, activation type, actor, reason and recorded quote notes

The token represents the complete publication and mode. It is stable across
age changes, repeated responses, and different requested subsets; a new
publication or mode changes it.

Future Billing calls:

```python
confirmed = price_service.get_billing_price_set(required_karat_ids)
# Employee reviews these exact Decimal values and acknowledges warnings.
# Inside Billing's EXISTING transaction, before inventory/ledger row locks:
current = price_service.lock_and_validate_for_finalization(
    connection, confirmed.price_token, required_karat_ids,
    acknowledged=employee_acknowledged_warning,
)
```

The finalization helper uses the caller's active transaction, holds settings
and active-row locks until the caller completes, and never commits, rolls
back, starts another transaction, or fetches a provider. Changed tokens require
another employee review. Newly stale/error-retained prices require explicit
acknowledgement; unavailable, rejected, incomplete, or implausibly future
prices cannot be finalized. Billing must compare the returned exact values
against its confirmed line snapshots and persist them in its existing snapshot
architecture. Billing persistence and Cash/Bank posting are unchanged in V1.

## Verification run

Final result: **179 tests passed**. Python compilation/import checks and
`git diff --check` passed. The price-specific tests include the stale local
error state clearing when another workstation publishes a new valid set.

Run from the project directory:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q main.py app config repositories services tests
git diff --check
```

Tests use fixtures/mocks or isolated SQLite; they do not post prices to the
live database or contact GoldTimo. Offscreen tests load the actual Main.qml.
Production MySQL schema and new repository SELECT/locking queries were also
verified without data writes. An authenticated live GoldTimo fetch and a
manual activation against live MySQL were not performed during development.

## Exact manual acceptance tests

Use an isolated test database for tests that activate prices or alter grants.
Start the application with `.\.venv\Scripts\python.exe main.py`.

1. **Initial state and layout:** sign in, click the top-bar price area, and
   confirm it opens the gold-price panel. With empty price tables it shows
   unavailable and no fabricated numeric price. Check 1080×680 and 1280×800:
   Cairo, Arabic right alignment, dark/gold style, scrolling controls, and
   the physical sidebar on the right.
2. **Manual preview:** as a user with `SETTINGS_MANAGE`, enter BUY `6125`,
   SELL `6225`, and reason `اختبار التسعير اليدوي`. Click preview. Expect:

   | Karat | SHOP_BUY | SHOP_SELL |
   | --- | --- | --- |
   | 18 | 5250.0000 | 5335.7143 |
   | 21 | 6125.0000 | 6225.0000 |
   | 24 | 7000.0000 | 7114.2857 |

   Preview must not activate prices. Change either input and verify the old
   preview/confirmation becomes invalid. Blank reason, zero, negative,
   nonnumeric or sell-below-buy values must be rejected.
3. **Manual activation:** preview again, then click the explicit confirmation.
   Expect mode MANUAL, source manual, three exact prices, and recorded times.
   Read the three latest quote/activation rows and `GOLD_PRICE_ACTIVATED` /
   `GOLD_PRICE_MODE_CHANGED` audit events: one quote group, real signed-in
   user, fresh optional employee, supplied reason, and derivation metadata.
   Older rows must remain unchanged. Refresh/restart and confirm the same
   active set returns. Automatic refresh must not replace it.
4. **Read-only user and permission revocation:** sign in as a user with
   `APP_LOGIN` but no `SETTINGS_MANAGE`. Prices/panel remain viewable; manual
   and AUTO controls are hidden. In a test fixture, revoke management permission
   after an authorized user's preview but before confirmation. Confirmation
   must fail without inserting quotes/activations or changing mode.
5. **Explicit AUTO:** configure a documented provider and select its source;
   make its valid response fall within the configured deviation threshold.
   Click return to automatic. Cancel the confirmation once: mode stays MANUAL.
   Confirm it: all three prices activate together, AUTO is shown, and the
   initiating user is audited. Remove access to the provider and retry from
   MANUAL: mode and the last manual set must remain unchanged.
6. **Provider failure/fallback:** in the provider fixture, return a timeout or
   invalid currency/unit/reversed spread for primary. With a valid configured
   fallback, expect its complete set and fallback warning. Fail both: expect
   ERROR, visible warning, and unchanged last active prices. Restore a valid
   newer response: expect FRESH and a new token/publication.
7. **Age/repeated timestamp:** use a fixture that repeatedly returns the same
   provider timestamp. Verify age increases and publication/token/fetch time
   do not change. After the stale threshold, expect a stale warning; a failed
   refresh may display ERROR while retaining the same prices. A future date,
   older timestamp, or changed values with unchanged timestamp must not be
   adopted as fresh.
8. **Responsiveness/logout:** use a delayed bounded provider response. While
   refresh is running, navigate/open dialogs and log out. The GUI remains
   responsive, repeated refresh clicks do not overlap, and logout closes the
   operational window. Log in as another user before the old response finishes:
   old results and old manual payloads must not appear or commit as the new user.
9. **Atomicity/Billing preparation:** run the repository/service test modules
   above for second-karat rollback, concurrent manual/automatic publication,
   token stability/change, stale acknowledgement and caller-owned transaction
   locking. Do not exercise current invoice finalization as an engine test;
   Billing V2 integration is intentionally pending.
