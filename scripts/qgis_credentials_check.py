# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Isolated QGIS credential, restart, UI and request checks; zero provider calls.

Run with QGIS' bundled Python. The parent creates a disposable profile before
any QGIS import. Child processes never use the user's authentication database.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def child(phase):
    import json
    import unittest
    from unittest.mock import Mock
    from qgis.PyQt.QtCore import QByteArray, QEventLoop, QSettings, QTimer, QUrl
    from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
    from qgis.PyQt.QtWidgets import QLineEdit, QMainWindow
    from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsProject, QgsSettings
    from qgis.gui import QgsMapCanvas
    from mapstyle_ai.credentials import AuthenticationUnavailable, CredentialStore
    from mapstyle_ai.providers import GOOGLE, OPENAI, MODELS
    from mapstyle_ai.generation import GenerationQueue
    from mapstyle_ai.panel import BackgroundPanel

    root = Path(os.environ['MAPSTYLE_AUTH_TEST_ROOT'])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(root/'config'))
    app = QgsApplication([], False, str(root/'config'))
    app.initQgis()
    manager = app.authManager()
    assert str(root) in manager.authenticationDatabaseUri()
    assert str(root) in QgsSettings().fileName()
    manager.setPasswordHelperEnabled(False)
    store = CredentialStore()
    secrets = {OPENAI: 'offline-openai-secret-123', GOOGLE: 'offline-google-secret-456'}
    password = 'disposable-QGIS-test-password'

    def unlock():
        assert manager.setMasterPassword(password, True)

    def header(provider, authcfg):
        request = store.apply(QNetworkRequest(QUrl('https://api.openai.com/' if provider == OPENAI
                                                  else 'https://generativelanguage.googleapis.com/')), provider, authcfg)
        name = b'Authorization' if provider == OPENAI else b'x-goog-api-key'
        assert not request.rawHeader(b'x-goog-api-key' if provider == OPENAI else b'Authorization')
        return bytes(request.rawHeader(name)).decode()

    if phase == 'write':
        unlock()
        for provider, secret in secrets.items():
            store.save(provider, secret)
        print('PASS: real QGIS encrypted storage created for both providers')
        return
    if phase == 'read':
        assert not manager.masterPasswordIsSet()
        ids = {provider: store.config_id(provider) for provider in secrets}
        assert all(ids.values()) and len(set(ids.values())) == 2
        assert not manager.masterPasswordIsSet(), 'metadata lookup must not unlock storage'
        unlock()
        for provider, secret in secrets.items():
            key, cfg = store.resolve(provider, '')
            assert not key and cfg == ids[provider]
            assert header(provider, cfg) == ('Bearer ' if provider == OPENAI else '') + secret
        # QGIS encrypts configuration payloads, not necessarily every metadata row.
        for path in (root/'auth').glob('*'):
            if path.is_file():
                assert all(secret.encode() not in path.read_bytes() for secret in secrets.values())
        settings = QgsSettings()
        settings.sync()
        assert all(secret not in Path(settings.fileName()).read_text() for secret in secrets.values())
        print('PASS: separate process restored both keys; auth storage and settings contain no plaintext keys')
        return

    unlock()
    window = QMainWindow()
    canvas = QgsMapCanvas()

    class Interface:
        def mainWindow(self): return window
        def mapCanvas(self): return canvas

    class Reply(QNetworkReply):
        def __init__(self, request, payload):
            super().__init__()
            self.setRequest(request)
            self.setUrl(request.url())
            self.setAttribute(QNetworkRequest.HttpStatusCodeAttribute, 403)
            self.payload = QByteArray(payload)
            QTimer.singleShot(0, self.finished.emit)

        def readAll(self):
            payload, self.payload = self.payload, QByteArray()
            return payload

        def abort(self): pass
        def readData(self, size): return b''

    class CredentialChecks(unittest.TestCase):
        def setUp(self):
            for cfg in list(manager.availableAuthMethodConfigs()):
                assert manager.removeAuthenticationConfig(cfg)
            QgsSettings().clear()
            unlock()

        def test_replacement_and_provider_isolation(self):
            first = store.save(OPENAI, secrets[OPENAI])
            google = store.save(GOOGLE, secrets[GOOGLE])
            assert header(OPENAI, first) == 'Bearer ' + secrets[OPENAI]
            assert store.save(OPENAI, 'replacement-openai') == first
            assert len(manager.availableAuthMethodConfigs()) == 2
            assert header(OPENAI, first) == 'Bearer replacement-openai'
            assert header(GOOGLE, google) == secrets[GOOGLE]
            assert store.resolve(OPENAI, 'session-override') == ('session-override', '')
            assert header(OPENAI, first) == 'Bearer replacement-openai'

        def test_cancelled_unlock_disabled_auth_and_failed_save(self):
            cfg = store.save(OPENAI, secrets[OPENAI])
            proxy = Mock(wraps=manager)
            proxy.masterPasswordIsSet.return_value = False
            proxy.setMasterPassword.return_value = False
            locked = CredentialStore(proxy)
            assert locked.config_id(OPENAI) == cfg
            proxy.setMasterPassword.assert_not_called()
            with self.assertRaises(AuthenticationUnavailable):
                locked.resolve(OPENAI, '')
            proxy.setMasterPassword.assert_called_once_with(True)
            proxy.updateAuthenticationConfig.assert_not_called()
            proxy.isDisabled.return_value = True
            assert not locked.config_id(OPENAI)
            with self.assertRaises(AuthenticationUnavailable):
                locked.save(GOOGLE, secrets[GOOGLE])
            failed = Mock(wraps=manager)
            failed.updateAuthenticationConfig.return_value = False
            with self.assertRaises(ValueError):
                CredentialStore(failed).save(OPENAI, 'replacement')
            assert header(OPENAI, cfg) == 'Bearer ' + secrets[OPENAI]
            failed_request = Mock(wraps=manager)
            failed_request.updateNetworkRequest.return_value = (False, QNetworkRequest())
            with self.assertRaises(ValueError):
                CredentialStore(failed_request).apply(QNetworkRequest(), OPENAI, cfg)
            failed_store = Mock(wraps=manager)
            failed_store.storeAuthenticationConfig.return_value = (False, QgsAuthMethodConfig())
            with self.assertRaises(ValueError):
                CredentialStore(failed_store).save(GOOGLE, secrets[GOOGLE])
            assert not store.config_id(GOOGLE)

        def test_deleted_wrong_provider_and_invalid_input(self):
            cfg = store.save(OPENAI, secrets[OPENAI])
            QgsSettings().setValue('mapstyle_ai/auth/google', cfg)
            assert not store.config_id(GOOGLE)
            with self.assertRaises(ValueError):
                store.apply(QNetworkRequest(QUrl('https://generativelanguage.googleapis.com/')), GOOGLE, cfg)
            assert manager.removeAuthenticationConfig(cfg)
            assert not store.config_id(OPENAI)
            with self.assertRaises(ValueError):
                store.resolve(OPENAI, '')
            with self.assertRaises(ValueError):
                store.save(OPENAI, 'invalid\nkey')
            assert not manager.availableAuthMethodConfigs()

        def test_panel_separate_inputs_save_empty_partial_failure_and_shutdown(self):
            panel = BackgroundPanel(Interface())
            panel._suppress_notifications = True
            assert len(panel.key_inputs) == 2
            for field in panel.key_inputs.values():
                assert field.echoMode() == QLineEdit.Password
            panel.key_inputs[OPENAI].setText(secrets[OPENAI])
            panel.key_inputs[GOOGLE].setText(secrets[GOOGLE])
            panel.provider.setCurrentText(OPENAI)
            assert panel.key_inputs[GOOGLE].text() == secrets[GOOGLE]
            panel.save_keys_button.click()
            assert all(not field.text() for field in panel.key_inputs.values())
            assert all(label.text() == 'Saved in QGIS' for label in panel.key_status.values())
            ids = {p: store.config_id(p) for p in secrets}
            panel.key_inputs[OPENAI].setText('replacement-openai')
            panel.save_keys_button.click()
            assert store.config_id(OPENAI) == ids[OPENAI]
            assert header(GOOGLE, ids[GOOGLE]) == secrets[GOOGLE]
            panel.show_key.setChecked(True)
            assert all(not field.text() for field in panel.key_inputs.values())
            assert all(field.echoMode() == QLineEdit.Normal for field in panel.key_inputs.values())
            panel.key_inputs[OPENAI].setText('invalid key')
            panel.key_inputs[GOOGLE].setText('replacement-google')
            panel.save_keys_button.click()
            assert panel.key_inputs[OPENAI].text() == 'invalid key'
            assert not panel.key_inputs[GOOGLE].text()
            assert header(GOOGLE, ids[GOOGLE]) == 'replacement-google'
            assert 'invalid key' not in panel.log.toPlainText()
            panel.shutdown()
            assert all(not field.text() for field in panel.key_inputs.values())
            assert all(store.config_id(p) == ids[p] for p in secrets)
            panel.deleteLater()

        def test_cancelled_save_does_not_repeat_dialog_or_clear_fields(self):
            panel = BackgroundPanel(Interface())
            panel._suppress_notifications = True
            for provider, secret in secrets.items():
                panel.key_inputs[provider].setText(secret)
            proxy = Mock(wraps=manager)
            proxy.masterPasswordIsSet.return_value = False
            proxy.setMasterPassword.return_value = False
            panel.credentials = CredentialStore(proxy)
            panel.save_keys_button.click()
            proxy.setMasterPassword.assert_called_once_with(True)
            assert all(panel.key_inputs[p].text() == s for p, s in secrets.items())
            assert not manager.availableAuthMethodConfigs()
            panel.shutdown()
            panel.deleteLater()

        def test_real_qgis_header_in_queue_and_echo_redaction(self):
            for provider, secret in secrets.items():
                cfg = store.save(provider, secret)
                source = root/'source.png'
                source.write_bytes(b'fake-source-image')
                network = Mock()
                def post(request, payload):
                    expected = ('Bearer ' if provider == OPENAI else '') + secret
                    name = b'Authorization' if provider == OPENAI else b'x-goog-api-key'
                    assert bytes(request.rawHeader(name)).decode() == expected
                    assert not request.rawHeader(b'x-goog-api-key' if provider == OPENAI else b'Authorization')
                    assert request.attribute(QNetworkRequest.RedirectPolicyAttribute) == QNetworkRequest.ManualRedirectPolicy
                    assert secret.encode() not in bytes(payload)
                    return Reply(request, json.dumps({'error': {'message': 'Echo: '+secret}}).encode())
                network.post.side_effect = post
                job = dict(id='A1', input=str(source), pixels=1024, output=str(root/'never-written.png'))
                queue = GenerationQueue([job], provider, MODELS[provider][0], '', 'prompt', b'style', 1,
                                        authcfg=cfg, network=network)
                errors = []
                queue.failed.connect(lambda tile, message: errors.append(message))
                loop = QEventLoop()
                queue.finished.connect(loop.quit)
                QTimer.singleShot(3000, loop.quit)
                queue.start()
                loop.exec_()
                network.post.assert_called_once()
                assert errors and 'HTTP 403' in errors[0] and secret not in errors[0], errors
                assert not queue.active and not queue._key and not queue.running
                # External deletion must prevent any network dispatch.
                assert manager.removeAuthenticationConfig(cfg)
                network.reset_mock()
                queue = GenerationQueue([job], provider, MODELS[provider][0], '', 'prompt', b'style', 1,
                                        authcfg=cfg, network=network)
                queue.start()
                queue._pump()
                network.post.assert_not_called()
                queue.shutdown()

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CredentialChecks))
    QgsProject.instance().clear()
    if not result.wasSuccessful():
        raise SystemExit(1)


def main():
    if len(sys.argv) > 1:
        child(sys.argv[1])
        return
    with tempfile.TemporaryDirectory(prefix='mapstyle-credentials-') as directory:
        root = Path(directory)
        (root/'auth').mkdir()
        (root/'config').mkdir()
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', MAPSTYLE_AUTH_TEST_ROOT=directory,
                   QGIS_AUTH_DB_DIR_PATH=str(root/'auth'), QGIS_CUSTOM_CONFIG_PATH=str(root/'config'))
        for phase in ('write', 'read', 'checks'):
            subprocess.run([sys.executable, str(Path(__file__).resolve()), phase], env=env, check=True, timeout=60)
    print('PASS: isolated QGIS credentials, restart, UI, failure handling and dispatch; provider calls: 0')


if __name__ == '__main__':
    main()
