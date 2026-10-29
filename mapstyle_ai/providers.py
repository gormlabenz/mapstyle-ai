# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Provider wire formats. No QGIS, filesystem, credentials storage or HTTP client."""
import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass

OPENAI = 'OpenAI · GPT Image'
GOOGLE = 'Google Gemini · Nano Banana'
# Square output only. Reviewed 2026-10-29 against:
# https://developers.openai.com/api/docs/guides/image-generation
# https://developers.openai.com/api/reference/resources/images/methods/edit
# https://ai.google.dev/gemini-api/docs/generate-content/image-generation
@dataclass(frozen=True)
class Capability:
    sizes: tuple
    default: int = 1024
    size_parameter: bool = True
    custom_square: bool = False


CAPABILITIES = {
    OPENAI: {
        model: Capability((1024, 2048), custom_square=True)
        for model in ('gpt-image-2.5-sunburst', 'gpt-image-2.5-flare', 'gpt-image-2',
                      'gpt-image-2.5-sunburst-2026-09-08',
                      'gpt-image-2.5-flare-2026-09-08', 'gpt-image-2-2026-04-21')
    },
    GOOGLE: {
        'gemini-nano-banana-2.1': Capability((1024, 2048, 4096)),
        'gemini-3-pro-image': Capability((1024, 2048, 4096)),
        'gemini-3.1-flash-image': Capability((512, 1024, 2048, 4096)),
        'gemini-2.5-flash-image': Capability((1024,), size_parameter=False),
    },
}
MODELS = {provider: list(models) for provider, models in CAPABILITIES.items()}
GOOGLE_SIZES = {512: '512', 1024: '1K', 2048: '2K', 4096: '4K'}
OPENAI_URL = 'https://api.openai.com/v1/images/edits'
GOOGLE_URL = 'https://generativelanguage.googleapis.com/v1/models/{model}:generateContent'


def validate_options(provider, model, pixels):
    if provider not in CAPABILITIES:
        raise ValueError('Unknown image provider.')
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,200}', model):
        raise ValueError('Enter a valid model ID without a URL or spaces (maximum 200 characters).')
    if type(pixels) is not int or not 256 <= pixels <= 8192:
        raise ValueError('Tile size must be a whole number between 256 and 8192 pixels.')
    capability = CAPABILITIES[provider].get(model)
    if capability and not capability.custom_square and pixels not in capability.sizes:
        raise ValueError(f'{model} supports square tiles of {capability.sizes} pixels only.')
    if provider == GOOGLE and pixels not in GOOGLE_SIZES:
        raise ValueError('The Gemini imageSize API supports square sizes 512, 1024, 2048 or 4096 here.')
    if provider == OPENAI:
        if pixels % 16:
            raise ValueError('OpenAI tile size must be a multiple of 16 pixels.')
        if capability and (pixels > 3840 or not 655360 <= pixels * pixels <= 8294400):
            raise ValueError('This OpenAI model requires 655360–8294400 pixels total and edges at most 3840 px.')


def compatible_size(provider, model, current):
    capability = CAPABILITIES[provider][model]
    return current if current in capability.sizes else capability.default


def resolve_options(provider, selected_model, selected_pixels, model_override='', size_override=''):
    """Unknown models need an explicit size; never inherit native capabilities."""
    model = model_override.strip() or selected_model
    raw_size = size_override.strip()
    if raw_size and not re.fullmatch(r'[0-9]{1,5}', raw_size):
        raise ValueError('Tile size override must be a whole pixel count, for example 2048.')
    if model not in CAPABILITIES.get(provider, {}) and not (model_override.strip() and raw_size):
        raise ValueError('Unknown models require both a model ID override and an explicit tile size override.')
    pixels = int(raw_size) if raw_size else selected_pixels
    validate_options(provider, model, pixels)
    return model, pixels


def validate_key(key):
    if not key.strip() or any(char.isspace() for char in key.strip()) or '\n' in key or '\r' in key:
        raise ValueError('Enter a valid API key without spaces or line breaks.')
    return key.strip()


def validate_image_count(provider, model, reference_count, neighbor_count=0):
    # Limits include the geographic input and finished neighbors, not only style images.
    # https://developers.openai.com/api/reference/resources/images/methods/edit
    # https://ai.google.dev/gemini-api/docs/image-generation
    count = 1 + reference_count + neighbor_count
    limit = 16 if provider == OPENAI else (14 if model.startswith((
        'gemini-nano-banana-', 'gemini-3-pro-image', 'gemini-3.1-flash-image',
        'gemini-3.1-flash-lite-image')) else None)
    if limit is not None and count > limit:
        raise ValueError(f'{model} accepts at most {limit} input images. This selection requires '
                         f'{count} ({reference_count} style references, 1 map input and '
                         f'{neighbor_count} neighbors). Remove reference images or choose another model.')
    if provider == GOOGLE and model.startswith('gemini-3-pro-image') and reference_count > 3:
        raise ValueError('Gemini 3 Pro Image supports up to 3 style references. '
                         'Remove reference images or choose another model.')


def make_request(provider, model, key, prompt, pixels, source, references, quality='auto', neighbors=()):
    validate_options(provider, model, pixels)
    # None explicitly defers authentication to QgsAuthManager at dispatch time.
    if key is not None:
        key = validate_key(key)
    references = (references,) if isinstance(references, bytes) else tuple(references)
    neighbors = tuple(neighbors)
    if not prompt.strip() or not source or any(not image for image in references):
        raise ValueError('A resolved prompt and readable input images are required.')
    validate_image_count(provider, model, len(references), len(neighbors))
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
        for index, reference in enumerate(references, 1):
            part('image[]', reference, f'style-reference-{index}.png')
        for index, neighbor in enumerate(neighbors, 1):
            part('image[]', neighbor, f'neighbor-{index}.png')
        body.extend(f'--{boundary}--\r\n'.encode())
        headers = {'Content-Type': 'multipart/form-data; boundary=' + boundary}
        if key is not None:
            headers['Authorization'] = 'Bearer ' + key
        return OPENAI_URL, headers, bytes(body)
    config = {'aspectRatio': '1:1'}
    capability = CAPABILITIES[GOOGLE].get(model)
    if capability is None or capability.size_parameter:
        config['imageSize'] = GOOGLE_SIZES[pixels]
    parts = [{'text': prompt}]
    for png in (source, *references, *neighbors):
        parts.append({'inlineData': {'mimeType': 'image/png', 'data': base64.b64encode(png).decode('ascii')}})
    body = json.dumps({'contents': [{'role': 'user', 'parts': parts}],
                       'generationConfig': {'responseModalities': ['TEXT', 'IMAGE'], 'imageConfig': config}}).encode()
    if len(body) > 20 * 1024 * 1024:
        raise ValueError('Gemini request exceeds 20 MB. Choose a smaller reference image.')
    headers = {'Content-Type': 'application/json'}
    if key is not None:
        headers['x-goog-api-key'] = key
    return GOOGLE_URL.format(model=model), headers, body


def parse_response(provider, payload):
    """Return image bytes, or an actionable error. Never download result URLs."""
    try:
        response = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('The provider returned invalid JSON.') from error
    if not isinstance(response, dict):
        raise ValueError('Unexpected provider response format.')
    if response.get('error'):
        error = response['error']
        message = error.get('message', 'API error') if isinstance(error, dict) else str(error)
        raise ValueError(message)
    encoded = None
    if provider == OPENAI:
        for item in response.get('data', []):
            if item.get('b64_json'):
                encoded = item['b64_json']
                break
        if not encoded:
            raise ValueError('OpenAI returned no image. Choose a GPT Image model with Base64 output.')
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
            raise ValueError('Gemini returned no image.' + (f' Reason: {reason}' if reason else ''))
    try:
        result = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, binascii.Error) as error:
        raise ValueError('The provider image data is corrupt.') from error
    if not result:
        raise ValueError('The provider returned empty image data.')
    return result


def safe_error(message, key=''):
    text = str(message)
    keys = (key,) if isinstance(key, str) else key
    for secret in sorted(filter(None, keys), key=len, reverse=True):
        text = text.replace(secret, '[API key removed]')
    # Provider/proxy errors can echo credentials or request bodies.
    text = re.sub(r'(?i)(Bearer\s+)[^\s"<>]+', r'\1[removed]', text)
    text = re.sub(r'(?i)([?&]key=)[^&\s]+', r'\1[removed]', text)
    text = re.sub(r'(?i)(x-goog-api-key[\s\"\x27:=]+)[^\s\"\x27<>]+', r'\1[removed]', text)
    return text[:1200]
