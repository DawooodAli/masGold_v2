# GoldTimo Settings UI

No live configuration, credentials, price history or schema was modified during
implementation. No Billing/Cash changes. No commit.

## Implementation map

- services/secret_store.py → SecretStore → set_secret/get_secret/delete_secret:
  keyring OS backend, Windows WinVaultKeyring, sanitized failures, no plaintext
  fallback. Credential identity masGold / goldtimo_api_key.
- config/gold_price_config.py → GoldTimoConfig → from_env:
  stored credential first, optional environment fallback, default timeout 10s.
- repositories/gold_price_repository.py → GoldPriceRepository →
  get_provider_options/save_provider_settings: existing source notes JSON,
  singleton settings transaction, before/after audit without secrets. Source ID
  resolved by code. Existing mode/fallback/reference/active rows preserved.
- services/gold_price_settings_service.py → GoldPriceSettingsService →
  load/save/delete/test: fresh authorization from the captured real session,
  URL allowlist, connection validation and explicit cross-store failure handling.
- services/gold_price_diagnostic.py → DiagnosticProvider/diagnose:
  shared fetch-only validation (moved out of CLI tool for service reuse).
- services/gold_price_service.py → GoldPriceService → _fetch:
  resolve saved connection options and OS credentials for every production fetch.
- app/viewmodels/gold_price_settings_viewmodel.py → SettingsWorker.run and
  GoldPriceSettingsViewModel.load/save/test/delete: background operations,
  session-bound result delivery, clearSecret on save/logout, sanitized logging.
  Test retains the masked draft so the following Save uses the tested key;
  navigation also clears the field in QML.
- main.py → ApplicationController → __init__/_open_operational:
  controller-owned settings worker lifetime, QML context, shutdown handling.
- qml/Main.qml → SettingsPage/GoldProviderSettings:
  compact scrollable Arabic/Cairo/right-aligned form, masked new key input,
  saved-state indicator, test results, save/test/delete actions.
- requirements,txt → dependency list: keyring>=25.7,<26, installed in project venv.

## Intended save behavior

Saving selects/enables GOLDTIMO as primary. The form explicitly states this;
fallback/reference remain unchanged. URL/timeout are whitelisted non-secret
metadata; the key never enters repository parameters or audit JSON. Save does
not request a price refresh or switch mode. Existing AUTO scheduling, if already
enabled by the user, resolves the new configuration on its next fetch.

The configuration transaction commits before any new key is stored. If it fails,
the key is untouched. If the OS write fails afterward, the UI explicitly reports
the partial save. These independent storage systems do not offer distributed
atomic commit. Deletion leaves MANUAL usable and does not erase developer env
configuration or existing price history.

## Validation

Tests mock keyring (never save a test credential in the actual OS vault), use an
isolated SQL fixture for configuration/audit checks and exercise the actual QML
page offscreen at 1440x900, 1280x800 and 1080x680. They cover credential precedence,
restart-style resolution, fresh permission revocation, unchanged MANUAL price
history, fetch-only behavior, unsaved key priority, secret-free DB/log output,
partial-save failures and GUI-thread isolation/password clearing. The later
credential trace adds an actual Test → Save regression check: the tested masked
key reaches Save before the field clears.

Full suite: 204 tests passed. The small-screen connection button was also tested
with a physical mouse click after scrolling, not just direct signal invocation.
Python compile and diff checks passed. QML lint exits 0; existing unqualified
context/outer-component warnings remain. No new non-unqualified warnings in the
provider panel. Real provider connectivity still requires an actual API key;
this implementation did not activate a live AUTO set.
