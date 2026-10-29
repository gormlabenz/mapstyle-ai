# QGIS credential integration

MapStyle AI 0.6.1 uses the QGIS 3.44 authentication infrastructure for optional persistent OpenAI and Google Gemini API keys. It requires no new Python dependencies and retains the existing HTTPS endpoints, manual redirect policy, TLS checks and generation timeouts.

## Storage and lifecycle

- Use the existing `QgsApplication.authManager()` singleton, with one `QgsAuthMethodConfig('APIHeader', 2)` per provider. Its configuration map holds `Authorization: Bearer <key>` for OpenAI or `x-goog-api-key: <key>` for Gemini. QGIS encrypts credential payloads in its authentication storage.
- Normal profile settings store only `mapstyle_ai/auth/openai` and `mapstyle_ai/auth/google` configuration IDs. IDs come from the manager after a successful store. Replacement uses `updateAuthenticationConfig()` and clears that configuration's authentication-method cache.
- Status checks use only `availableAuthMethodConfigs()` metadata and validate the ID's method and provider-specific name. They never load decrypted configurations into inputs. External removal or a reference belonging to the other provider cannot silently select another key.
- The shared save button processes nonempty fields independently. Invalid or failed entries remain in the inputs. Successful entries clear. An unavailable authentication system or cancelled unlock stops the shared save attempt to avoid repeated master-password prompts.
- The plugin calls the interactive `setMasterPassword(True)` overload when needed. QGIS owns master-password setup, verification and optional operating-system Wallet/Keyring integration. The plugin never sets a fixed master password, changes the wallet preference or stores a password file.
- Generation uses a new session input in preference to a saved configuration. Saved credentials are passed through the orchestration as `authcfg` and expanded using `updateNetworkRequest()` immediately before dispatch. Binding return values, including returned request/config objects, are checked. Stored keys do not pass through the panel's intent or manifest.
- An in-flight reply retains the applied key in memory solely for redacting provider errors that echo it. Plugin logs and exported manifests omit credentials. This does not change QGIS' own diagnostic logging configuration; QGIS debug builds can log authentication headers.
- Unloading clears session inputs. The reload helper preserves only entered session values in memory and supports migration from the previous single-field panel. Saved configurations remain managed by QGIS.

## Official references checked

- [PyQGIS authentication cookbook](https://docs.qgis.org/3.44/en/docs/pyqgis_developer_cookbook/authentication.html): manager access, configuration storage, APIHeader support and late credential expansion.
- [QgsAuthManager 3.44 API](https://qgis.org/pyqgis/3.44/core/QgsAuthManager.html): metadata queries, interactive master-password overload, configuration create/update, cache clearing and network-request expansion.
- [QGIS authentication overview](https://docs.qgis.org/3.44/en/docs/user_manual/auth_system/auth_overview.html): encrypted credential storage and optional Wallet/Keyring integration.
- [QGIS 3.44 APIHeader source](https://github.com/qgis/QGIS/blob/release-3_44/src/auth/apiheader/core/qgsauthapiheadermethod.cpp): method version 2, direct header-name/value mapping and authentication-method caching.

## Verification

Run `/Applications/QGIS.app/Contents/MacOS/python scripts/qgis_credentials_check.py` on this Mac. The runner creates disposable configuration and authentication directories before importing QGIS, disables the password helper only in this test profile, and starts separate writer/reader processes. It checks real encrypted storage, restart persistence, header expansion, replacement and provider isolation. Additional tests cover session precedence, absent/wrong references, failed saves, simulated unlock cancellation and disabled authentication, both UI fields, partial saves, unload behavior, and the generation queue with real QGIS auth expansion and simulated HTTP replies. No provider request is sent and the user's profile is untouched.

The ordinary provider tests check deferred authentication, invalid input and error redaction. The existing QGIS compact UI, metric grid/export and result-variant checks remain applicable. Native master-password and OS-wallet dialogs need a manual desktop check; their failure branches are simulated in the automated suite.

Verified with QGIS **3.44.15-Solothurn**: 44 ordinary unit tests, 6 QGIS credential checks, separate-process persistence checks, compact UI generation/cancellation with session and stored keys, numbered-reference/text-only/mixed-style generation and reload checks, metric grid/GeoTIFF checks, and all result transition modes passed. The installable package is `dist/mapstyle_ai-0.6.1.zip`. No paid API call or installation into the user's QGIS profile was performed.
