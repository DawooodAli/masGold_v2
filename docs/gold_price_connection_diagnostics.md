# GoldTimo connection failure investigation — 2026-09-23

## Before

The installed GoldTimoProvider._normalize already required the documented
top-level `unit: oz`, copied gramSell to SHOP_BUY and gramBuy to SHOP_SELL,
and emitted normalized `unit: GRAM`. It did not require raw `unit: gram`.
The [current official example](https://goldtimo.com/eg/en/docs) confirms that
ounce reference price and per-gram buy/sell coexist. No unit conversion or
global validation relaxation was necessary.

The first sandboxed probe hit a network failure. Repeating with network access
at 19:40:10 UTC reproduced HTTP 403, before parsing or quote validation. A
separate read confirmed a credential exists in Windows Credential Manager.
The response content type was JSON, server was Cloudflare, and no Cloudflare
challenge marker was present. These observations do not prove whether the
provider rejected the key, account entitlement, or another access restriction.
No raw response body, authorization header or credential was displayed/logged.

The actual diagnostic defect had two stages: GoldPriceSettingsService.test
replaced diagnostic failures with a generic ValueError, then SettingsWorker.run
replaced that with the generic Arabic operation failure. Logging recorded only
the operation, losing HTTP status and validation category.

## After

The stored-key-only fetch at 19:42:32 UTC still returned HTTP 403. It now returns
`validation=REJECTED`, `error_code=HTTP_AUTH`, `http_status=403`, and the safe
Arabic message:

> رفض المصدر مفتاح API أو صلاحية الوصول (401/403). راجع المفتاح وصلاحيات الحساب.

No prices/timestamps were returned; no set was activated. The diagnostic fix
does not make the rejected credential authorized. Provider account/key access
needs checking if the same response persists in the application.

## File → Class/component → Method

- services/gold_price_provider.py → GoldTimoProvider → fetch/_http_error/_normalize:
  typed transport/status categories and explicit GoldTimo currency/karat checks.
  Existing ounce/per-gram mapping and common validator remain strict.
- services/gold_price_errors.py → ConnectionTestError:
  allowlisted codes, fixed Arabic messages, validated numeric HTTP status.
- services/gold_price_diagnostic.py → diagnose:
  preserve safe codes/status; missing-key/stale categories; no raw exception text.
- services/gold_price_settings_service.py → GoldPriceSettingsService → test:
  propagate category/status, distinguish unavailable credential store.
- app/viewmodels/gold_price_settings_viewmodel.py → SettingsWorker → run:
  log operation/code/status only and show the corresponding fixed Arabic message.
- tools/diagnose_gold_prices.py → main:
  distinguish credential-store from configuration errors in CLI diagnostics.

Categories include HTTP_AUTH (401/403), RATE_LIMIT (429), HTTP_ERROR, NETWORK,
TIMEOUT, KEY_MISSING, CREDENTIAL_STORE, INVALID_JSON, CURRENCY_MISMATCH,
KARAT_MISMATCH, SCHEMA_UNIT, QUOTE_VALIDATION, STALE, PERMISSION and INTERNAL.

Tests use the exact documented numeric example, malformed contract variants,
fake HTTP errors containing a test secret, credential-store failure, and the
service-to-worker propagation path. General normalized ounce units remain
rejected by existing validator tests. The existing UI mouse test now waits for
the modal close animation before clicking the Settings connection button.

All live probes were fetch-only, with no GoldPriceService refresh/activation
call and no DB writes. Billing, modes, quotes, activations and schema untouched.
