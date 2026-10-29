"""Provider wire formats. No QGIS, filesystem, credentials storage or HTTP client."""
import base64
import binascii
import json
import re
import uuid

OPENAI = 'OpenAI · GPT Image'
GOOGLE = 'Google Gemini · Nano Banana'
MODELS = {
    OPENAI: ['gpt-image-2.5-sunburst', 'gpt-image-2.5-flare', 'gpt-image-2'],
    GOOGLE: ['gemini-nano-banana-2.1', 'gemini-3-pro-image', 'gemini-3.1-flash-image'],
}
OPENAI_URL = 'https://api.openai.com/v1/images/edits'
GOOGLE_URL = 'https://generativelanguage.googleapis.com/v1/models/{model}:generateContent'


def validate_options(provider, model, pixels):
    if provider not in MODELS:
        raise ValueError('Unbekannter Bildanbieter.')
    if not re.fullmatch(r'[A-Za-z0-9._-]+', model):
        raise ValueError('Bitte eine gültige Modell-ID ohne URL oder Leerzeichen eingeben.')
    if pixels not in (1024, 2048):
        raise ValueError('Für die Generierung sind 1024 oder 2048 Pixel erforderlich.')
    if model.startswith('gemini-2.5-') and pixels != 1024:
        raise ValueError('Gemini 2.5 Flash Image unterstützt hier nur 1024-Pixel-Kacheln.')
    if model.startswith(('gpt-image-1', 'dall-e')) and pixels != 1024:
        raise ValueError('Für 2048-Pixel-Kacheln ein aktuelles GPT-Image-Modell wählen.')


def make_request(provider, model, key, prompt, pixels, source, reference, quality='auto', neighbors=()):
    validate_options(provider, model, pixels)
    if not key.strip() or '\n' in key or '\r' in key:
        raise ValueError('Bitte einen gültigen API-Key eingeben.')
    if not prompt.strip() or not source or not reference:
        raise ValueError('Prompt, Eingabebild und Referenzbild sind erforderlich.')
    if provider == OPENAI:
        boundary = 'ai-map-' + uuid.uuid4().hex
        body = bytearray()
        def part(name, value, filename=None):
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'.encode())
            if filename:
                body.extend(f'; filename="{filename}"\r\nContent-Type: image/png'.encode())
            body.extend(b'\r\n\r\n')
            body.extend(value if isinstance(value, bytes) else str(value).encode('utf-8'))
            body.extend(b'\r\n')
        for name, value in [('model', model), ('prompt', prompt), ('n', '1'),
                            ('size', f'{pixels}x{pixels}'), ('quality', quality),
                            ('output_format', 'png')]:
            part(name, value)
        part('image[]', source, 'source.png')
        part('image[]', reference, 'style-reference.png')
        for index, neighbor in enumerate(neighbors, 1):
            part('image[]', neighbor, f'neighbor-{index}.png')
        body.extend(f'--{boundary}--\r\n'.encode())
        return OPENAI_URL, {'Authorization': 'Bearer ' + key,
                            'Content-Type': 'multipart/form-data; boundary=' + boundary}, bytes(body)
    config = {'aspectRatio': '1:1'}
    if not model.startswith('gemini-2.5-'):
        config['imageSize'] = '1K' if pixels == 1024 else '2K'
    parts = [{'text': prompt}]
    for png in (source, reference, *neighbors):
        parts.append({'inlineData': {'mimeType': 'image/png', 'data': base64.b64encode(png).decode('ascii')}})
    body = json.dumps({'contents': [{'role': 'user', 'parts': parts}],
                       'generationConfig': {'responseModalities': ['TEXT', 'IMAGE'], 'imageConfig': config}}).encode()
    if len(body) > 20 * 1024 * 1024:
        raise ValueError('Gemini-Anfrage ist größer als 20 MB. Ein kleineres Referenzbild wählen.')
    return GOOGLE_URL.format(model=model), {'x-goog-api-key': key, 'Content-Type': 'application/json'}, body


def parse_response(provider, payload):
    """Return image bytes, or an actionable error. Never download result URLs."""
    try:
        response = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('Der Anbieter hat keine gültige JSON-Antwort geliefert.') from error
    if not isinstance(response, dict):
        raise ValueError('Unerwartetes Antwortformat des Anbieters.')
    if response.get('error'):
        error = response['error']
        message = error.get('message', 'API-Fehler') if isinstance(error, dict) else str(error)
        raise ValueError(message)
    encoded = None
    if provider == OPENAI:
        for item in response.get('data', []):
            if item.get('b64_json'):
                encoded = item['b64_json']
                break
        if not encoded:
            raise ValueError('OpenAI hat kein Bild geliefert. Ein GPT-Image-Modell mit Base64-Ausgabe wählen.')
    else:
        reasons = []
        for candidate in response.get('candidates', []):
            # Never mistake internal thought images for the final generated output.
            for part in candidate.get('content', {}).get('parts', []):
                inline = part.get('inlineData', part.get('inline_data', {}))
                if not part.get('thought') and inline.get('mimeType', inline.get('mime_type', '')).startswith('image/'):
                    encoded = inline.get('data')
                    if encoded:
                        break
            if encoded:
                break
            if candidate.get('finishReason'):
                reasons.append(candidate['finishReason'])
        if not encoded:
            reason = response.get('promptFeedback', {}).get('blockReason') or ', '.join(reasons)
            raise ValueError('Gemini hat kein Bild geliefert.' + (f' Grund: {reason}' if reason else ''))
    try:
        result = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, binascii.Error) as error:
        raise ValueError('Die Bilddaten des Anbieters sind beschädigt.') from error
    if not result:
        raise ValueError('Der Anbieter hat leere Bilddaten geliefert.')
    return result


def safe_error(message, key=''):
    text = str(message)
    if key:
        text = text.replace(key, '[API-Key entfernt]')
    # Provider/proxy errors can echo credentials or request bodies.
    text = re.sub(r'(?i)(Bearer\s+)[^\s"<>]+', r'\1[entfernt]', text)
    text = re.sub(r'(?i)([?&]key=)[^&\s]+', r'\1[entfernt]', text)
    return text[:1200]
