"""Bounded async HTTP queue on QGIS' event loop. No worker touches project layers."""
from pathlib import Path
import time

from qgis.PyQt.QtCore import QByteArray, QBuffer, QIODevice, QObject, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QImage
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.core import QgsNetworkAccessManager

from . import providers
from .credentials import CredentialStore, PROVIDERS

DEFAULT_TIMEOUT_MS = 30 * 60 * 1000


def configure_reply_timeout(reply, timeout_ms):
    """QGIS 3.44 has its own idle timer in addition to Qt's request timeout.

    Its per-reply timer is restarted on upload/download progress. Adjust only
    this reply; QgsNetworkAccessManager.setTimeout would change global settings.
    """
    qgis_timer = reply.findChild(QTimer, 'timeoutTimer')
    if qgis_timer is not None:
        qgis_timer.setInterval(timeout_ms)
    return qgis_timer is not None


def image_png(path):
    image = QImage(str(path))
    if image.isNull():
        raise ValueError(f'Cannot read image: {Path(path).name}')
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, 'PNG'):
        raise ValueError('Could not prepare image as PNG.')
    return bytes(buffer.data())


class GenerationQueue(QObject):
    started = pyqtSignal(str)
    succeeded = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)
    cancelled_tile = pyqtSignal(str)
    finished = pyqtSignal()
    MAX_RESPONSE = 96 * 1024 * 1024

    def __init__(self, jobs, provider, model, key, prompt, references, concurrency,
                 quality='auto', parent=None, network=None, timeout_ms=DEFAULT_TIMEOUT_MS,
                 authcfg=''):
        super().__init__(parent)
        self.pending = list(jobs)
        self.provider, self.model, self._key = provider, model, key
        self.authcfg = authcfg
        self.prompt, self.references = prompt, references
        self.concurrency, self.quality = concurrency, quality
        self.network = network or QgsNetworkAccessManager.instance()
        self.timeout_ms = timeout_ms
        self.running = {}
        self.active = False
        self.cancelled = False
        self.max_active = 0

    def start(self):
        self.active = True
        QTimer.singleShot(0, self._pump)

    def _pump(self):
        if not self.active:
            return
        while self.pending and len(self.running) < self.concurrency and not self.cancelled:
            job = self.pending.pop(0)
            redaction_key = self._key
            try:
                url, headers, payload = providers.make_request(
                    self.provider, self.model, None if self.authcfg else self._key, job.get('prompt', self.prompt), job['pixels'],
                    Path(job['input']).read_bytes(), self.references, self.quality,
                    neighbors=job.get('neighbors', ()))
                request = QNetworkRequest(QUrl(url))
                request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
                request.setTransferTimeout(self.timeout_ms)
                for name, value in headers.items():
                    request.setRawHeader(name.encode('ascii'), value.encode('utf-8'))
                if self.authcfg:
                    request = CredentialStore().apply(request, self.provider, self.authcfg)
                    redaction_key = bytes(request.rawHeader(PROVIDERS[self.provider][2].encode('ascii'))).decode('utf-8')
                    if self.provider == providers.OPENAI:
                        redaction_key = redaction_key.removeprefix('Bearer ')
                reply = self.network.post(request, QByteArray(payload))
                qgis_timeout_adjusted = configure_reply_timeout(reply, self.timeout_ms)
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda r=reply: self._timeout(r))
                self.running[reply] = dict(job=job, buffer=bytearray(), timer=timer, error='',
                                          started=time.monotonic(), qgis_timeout_adjusted=qgis_timeout_adjusted,
                                          redaction_key=redaction_key)
                reply.readyRead.connect(lambda r=reply: self._read(r))
                reply.finished.connect(lambda r=reply: self._done(r))
                timer.start(self.timeout_ms)
                self.max_active = max(self.max_active, len(self.running))
                self.started.emit(job['id'])
            except Exception as error:
                self.failed.emit(job['id'], providers.safe_error(error, redaction_key))
                if self.authcfg:
                    for pending in self.pending:
                        self.cancelled_tile.emit(pending['id'])
                    self.pending.clear()
        if not self.pending and not self.running and self.active:
            self.active = False
            self._key = ''
            self.references = ()
            self.finished.emit()

    def _read(self, reply):
        state = self.running.get(reply)
        if state is None:
            return
        data = bytes(reply.readAll())
        if len(state['buffer']) + len(data) > self.MAX_RESPONSE:
            state['error'] = 'Provider response exceeds 96 MB.'
            reply.abort()
        else:
            state['buffer'].extend(data)

    def _timeout(self, reply):
        state = self.running.get(reply)
        if state:
            state['error'] = (f'Time limit reached after {self.timeout_ms / 60000:g} minutes. '
                              'The provider may still charge for the request.')
            reply.abort()

    def _done(self, reply):
        self._read(reply)
        state = self.running.pop(reply, None)
        if state is None:
            return
        timer = state['timer']
        timer.stop()
        timer.deleteLater()
        job = state['job']
        try:
            if self.cancelled:
                self.cancelled_tile.emit(job['id'])
                return
            if state['error']:
                raise ValueError(state['error'])
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            payload = bytes(state['buffer'])
            if status and not 200 <= int(status) < 300:
                if int(status) in (401, 402, 403, 429):
                    # Don't send the rest of a large batch with invalid access or quota.
                    # Already running requests may still succeed and are kept.
                    queued, self.pending = self.pending, []
                    for pending in queued:
                        self.cancelled_tile.emit(pending['id'])
                detail = ''
                try:
                    providers.parse_response(self.provider, payload)
                except ValueError as error:
                    detail = ': ' + str(error)
                raise ValueError(f'HTTP {status}{detail}')
            if reply.error() != QNetworkReply.NoError:
                elapsed = time.monotonic() - state['started']
                raise ValueError(f'Network error after {elapsed:.0f} seconds: {reply.errorString()} '
                                 f'(Plugin time limit: {self.timeout_ms / 60000:g} minutes).')
            image = QImage.fromData(providers.parse_response(self.provider, payload))
            if image.isNull():
                raise ValueError('The provider returned no readable image.')
            path = Path(job['output'])
            if not image.save(str(path), 'PNG'):
                raise ValueError('Could not save generated image.')
            if image.width() != job['pixels'] or image.height() != job['pixels']:
                raise ValueError(f'Result is {image.width()} × {image.height()} instead of {job["pixels"]} × {job["pixels"]} pixels. Original saved without stretching.')
            self.succeeded.emit(job['id'], str(path))
        except Exception as error:
            self.failed.emit(job['id'], providers.safe_error(error, state['redaction_key']))
        finally:
            reply.deleteLater()
            QTimer.singleShot(0, self._pump)

    def cancel(self):
        self.cancelled = True
        for job in self.pending:
            self.cancelled_tile.emit(job['id'])
        self.pending.clear()
        for reply in list(self.running):
            reply.abort()
        QTimer.singleShot(0, self._pump)

    def shutdown(self):
        self.active = False
        self.pending.clear()
        for reply, state in list(self.running.items()):
            reply.readyRead.disconnect()
            reply.finished.disconnect()
            state['timer'].stop()
            state['timer'].deleteLater()
            reply.abort()
            reply.deleteLater()
        self.running.clear()
        self._key = ''
        self.references = ()
