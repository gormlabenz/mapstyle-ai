"""Provider credentials in QGIS' encrypted authentication storage.

Only opaque authcfg references belong in QgsSettings. Saved secrets are expanded
by QGIS into a network request, never into the panel's input fields.
"""
from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings

from .providers import GOOGLE, OPENAI, validate_key


PROVIDERS = {
    OPENAI: ('openai', 'MapStyle AI - OpenAI', 'Authorization'),
    GOOGLE: ('google', 'MapStyle AI - Google Gemini', 'x-goog-api-key'),
}


class AuthenticationUnavailable(ValueError):
    """Do not repeat an unsuccessful unlock for the next provider."""


class CredentialStore:
    def __init__(self, manager=None, settings=None):
        self.manager = manager if manager is not None else QgsApplication.authManager()
        self.settings = settings if settings is not None else QgsSettings()

    def _setting(self, provider):
        return 'mapstyle_ai/auth/' + PROVIDERS[provider][0]

    def config_id(self, provider):
        """Check public metadata only; opening a panel must not prompt to unlock."""
        if self.manager.isDisabled():
            return ''
        authcfg = self.settings.value(self._setting(provider), '', type=str)
        config = self.manager.availableAuthMethodConfigs().get(authcfg)
        if config and config.method() == 'APIHeader' and config.name() == PROVIDERS[provider][1]:
            return authcfg
        return ''

    def unlock(self):
        if self.manager.isDisabled():
            raise AuthenticationUnavailable('QGIS authentication is unavailable. Check Settings > Options > Authentication.')
        if 'APIHeader' not in self.manager.authMethodsKeys():
            raise AuthenticationUnavailable('The QGIS API Header authentication method is unavailable.')
        # Use the interactive overload. Never set or store a master password here.
        if not self.manager.masterPasswordIsSet() and not self.manager.setMasterPassword(True):
            raise AuthenticationUnavailable('QGIS authentication was not unlocked. No API request was sent.')

    def save(self, provider, key):
        key = validate_key(key)
        self.unlock()
        authcfg = self.config_id(provider)
        config = QgsAuthMethodConfig('APIHeader', 2)
        config.setName(PROVIDERS[provider][1])
        config.setConfig(PROVIDERS[provider][2], 'Bearer ' + key if provider == OPENAI else key)
        if authcfg:
            config.setId(authcfg)
            success = self.manager.updateAuthenticationConfig(config)
        else:
            # SIP versions differ in whether the in/out config is also returned.
            result = self.manager.storeAuthenticationConfig(config)
            if isinstance(result, tuple):
                success, config = result
            else:
                success = result
        if not success or not config.id():
            raise ValueError('QGIS could not save the API key. Check Settings > Options > Authentication.')
        self.settings.setValue(self._setting(provider), config.id())
        self.settings.sync()
        self.manager.clearCachedConfig(config.id())
        return config.id()

    def resolve(self, provider, entered_key):
        """An explicit session key wins; otherwise unlock the saved reference."""
        if entered_key:
            return validate_key(entered_key), ''
        authcfg = self.config_id(provider)
        if not authcfg:
            raise ValueError('Enter or save an API key for the selected provider.')
        self.unlock()
        return '', authcfg

    def apply(self, request, provider, authcfg):
        if not authcfg or self.config_id(provider) != authcfg:
            raise ValueError('The saved API key is no longer available. Save it again in Provider settings.')
        result = self.manager.updateNetworkRequest(request, authcfg)
        if isinstance(result, tuple):
            success, request = result
        else:
            success = result
        if not success or not request.rawHeader(PROVIDERS[provider][2].encode('ascii')):
            raise ValueError('QGIS could not apply the saved API key. No API request was sent.')
        return request
