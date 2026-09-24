# Stored credential/request trace — 2026-09-24

## Confirmed follow-up: direct transient versus stored key

The latest evidence supersedes the earlier unresolved boundary below. The
masked local prompt produced two 40-character strings whose exact comparison
(after strip on input) was **false**. No surrounding whitespace was present.
The transient entered key returned HTTP 200 through the production Python
transport; the stored key returned HTTP 401 through both Python and PowerShell.
This establishes a stored-credential mismatch. It does not establish when or
how the different key was originally saved.

Writer and reader both use service `masGold`, name `goldtimo_api_key`, with the
same WinVaultKeyring backend. Its primary target is `masGold`; its fallback is
`goldtimo_api_key@masGold`. Both targets exist and both rejected live 21K requests.
The selected stored credential had no quote characters, CR/LF or surrounding
whitespace. No stored credential was modified during this investigation.

A reproducible UI defect was also fixed: `GoldPriceSettingsViewModel.test`
emitted `clearSecret` before starting the request. The current test received the
correct captured string, but a following Save received an empty field and kept
the older stored key. Test now retains the **masked** draft so Save receives the
tested key. Save, navigation and logout still clear the field. This defect is a
possible explanation for a Test → Save mismatch; the user's original sequence
cannot be reconstructed from these observations.

### Latest fetch-only evidence

Recorded 2026-09-24, timestamps below are provider time UTC+03:00:

| Karat | SHOP_BUY (gramSell) | SHOP_SELL (gramBuy) | Provider timestamp | HTTP |
|---|---:|---:|---|---:|
| 18 | 5320.0000 | 5341.0000 | 2026-09-24T16:15:07+03:00 | 200 |
| 21 | 6207.0000 | 6232.0000 | 2026-09-24T16:15:08+03:00 | 200 |
| 24 | 7093.0000 | 7122.0000 | 2026-09-24T16:15:08+03:00 | 200 |

The strict combined publication validator still rejects this set because the
18K timestamp differs from the other two. Authentication succeeds for all three;
the timestamp rule remains unchanged. Individual prices are diagnostic
observations, not an approved publication. No AUTO activation, database writes,
quote/activation inserts, schema changes or Billing/Cash changes were performed.

Full safe output: `reports/goldtimo_credential_comparison.json`.

### Changed file → class → method

- `config/gold_price_config.py` → `GoldTimoConfig` → `from_env/__post_init__`:
  explicit ENTERED → KEYRING → ENV → NONE source selection; only strip mutates
  input. Embedded CR/LF is rejected as an invalid HTTP header. Internal case and
  characters are never rewritten. Optional stored comparison cannot block an
  entered key if the keyring is unavailable.
- `services/gold_price_settings_service.py` → `GoldPriceSettingsService` →
  `test/save`: safe entered/stored metadata and exact save read-back comparison.
- `app/viewmodels/gold_price_settings_viewmodel.py` →
  `GoldPriceSettingsViewModel` → `test`: preserve the masked draft for Save.
- `services/gold_price_provider.py` → `GoldTimoProvider` →
  `fetch/probe_21/_fetch_quotes/_log_request`: explicit transient-key bypass;
  21K-only preflight; allowlisted diagnostics. Existing HTTP transport, endpoint,
  price mapping and complete-set validation remain in use.
- `services/gold_price_diagnostic.py` → `DiagnosticProvider/diagnose`:
  retain safely typed per-endpoint observations even when set validation fails;
  distinguish a 21K transport preflight from a validated complete publication.
- `tools/diagnose_gold_prices.py` → `prompt_key/compare_entered/main`:
  hidden terminal or masked local GUI input, 21K-first comparison, no credential
  persistence and no DB imports. No command-line key argument is accepted.

Run a local comparison without saving a key:

```powershell
.\.venv\Scripts\python.exe -m tools.diagnose_gold_prices --prompt-key-window --compare-stored --trace
```

For temporary Settings request logging, set `MASGOLD_GOLDTIMO_TRACE=1` before
starting the app; unset it afterward. Diagnostics contain only entered/stored
presence and lengths, equality, leading/trailing whitespace, credential source,
official request URL, header name and HTTP status. Null stored fields in the
direct provider bypass mean storage was not consulted; the initial comparison
contains the actual comparison metadata. No credential values or hashes appear.

The stored credential still needs replacement through the authorized Settings
Save action using the known-working key. The diagnostic deliberately does not
save keys or change provider configuration.

### Verification

The gold-price suite passed 113 tests, including the actual QML Test → Save
regression. A final diagnostic edge-case guard (a malformed stored key cannot
block the successful direct-key complete fetch) then passed all 10 diagnostic
tests, including its new regression. Python compile/import checks and
`git diff --check` passed. No production database connection was opened by the
live diagnostic tools. No actual credential was logged, displayed or persisted.

## Earlier investigation (historical)

Actual path: Main.qml GoldProviderSettings ->
GoldPriceSettingsViewModel.test -> SettingsWorker.run ->
GoldPriceSettingsService.test -> GoldTimoConfig.from_env ->
SecretStore.get_secret -> GoldTimoProvider.fetch -> urllib HTTPS transport.
GoldPriceViewModel handles price activation, not the Settings connection test.
An entered unsaved key bypasses keyring and is passed to GoldTimoConfig directly.

## Observed evidence

The OS credential is a nonempty string, 40 characters. It has no surrounding
whitespace, quotation marks, bytes-representation wrapper or authorization
prefix. No key value was printed, displayed or logged. The original entered
length cannot be recovered retrospectively; the previous implementation did
not record it. No production credential was re-saved during this investigation.

Every probe targeted exactly:
https://goldtimo.com/api/v1/price/XAU/EGP/21

| Controlled request | HTTP status |
|---|---|
| Original urllib request | 403 |
| Direct HTTPS, X-Api-Key, Python urllib client identifier | 403 |
| Direct HTTPS, x-api-key, same Python client identifier | 403 |
| Direct HTTPS, x-api-key, explicit masGold client identifier | 401 |
| Updated production HTTPS handler, x-api-key, masGold identifier | 401 |

Header values were the unencoded raw OS credential, with no Bearer/ApiKey prefix.
Changing casing alone did not fix the refusal. Changing the client identifier
changed 403 to 401, but did not authorize the request. No response bodies were
printed. A successful 21K request was not obtained, so live 18/21/24 fetching
was not attempted. The user's external success does not establish the reason
for the remaining local refusal; do not claim the account is invalid or that
the credential mismatch is proven.

## Corrections and remaining boundary

- config/gold_price_config.py -> GoldTimoConfig.__post_init__: normalize outer
  whitespace on both entered and stored credentials; reject quotes, prefixes,
  embedded whitespace, non-ASCII and non-string values. No bytes-to-string repr.
- services/gold_price_settings_service.py -> GoldPriceSettingsService.save:
  save the normalized value, read it back, require exact equality, and report
  entered/read-back character counts only at INFO level. Any count difference
  due to outer whitespace is deliberate; an actual read-back mismatch fails.
- services/gold_price_provider.py -> _GoldTimoHTTPSConnection.request:
  preserve exact lowercase x-api-key on the wire despite urllib's title-casing.
  Existing TLS validation, proxy handling and redirect rejection remain.
- services/gold_price_provider.py -> GoldTimoProvider.fetch/_log_request:
  explicit masGold identifier and allowlisted INFO diagnostics only: configured
  boolean, character count, outer-whitespace boolean, official URL, header name,
  HTTP status. Arbitrary custom URL paths are omitted to avoid secret leakage.

Existing `unit: oz` handling and gramSell -> SHOP_BUY / gramBuy -> SHOP_SELL
remain unchanged. This fixes input/transport observability and robustness, but
does NOT establish a successful real connection. The next comparison must use
the known-working credential entered directly in Settings Test Connection;
after saving, read-back verification now proves whether storage changes it.
Never paste the credential into chat or a log.

No database changes, quote/activation writes, AUTO activation, Billing changes,
or commit. Tests cover whitespace normalization, wrappers/bytes rejection,
exact wire header/value, saved-key equality and secret-free length diagnostics.
