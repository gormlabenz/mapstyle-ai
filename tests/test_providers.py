# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

import base64
from email import policy
from email.parser import BytesParser
import json
import unittest

from mapstyle_ai.providers import (
    GOOGLE, OPENAI, make_request, parse_response, safe_error, validate_options,
    validate_key, CAPABILITIES, compatible_size, resolve_options,
)


class ProviderTests(unittest.TestCase):
    def test_openai_multipart_two_images_correct_order(self):
        url, headers, body = make_request(OPENAI, 'gpt-image-2.5-sunburst', 'dummy',
                                          'Style from reference', 2048, b'SOURCE', b'REFERENCE', 'high')
        message = BytesParser(policy=policy.default).parsebytes(
            ('Content-Type: ' + headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
        parts = list(message.iter_parts())
        fields = {part.get_param('name', header='content-disposition'): part.get_payload(decode=True)
                  for part in parts if not part.get_filename()}
        self.assertEqual(fields['size'], b'2048x2048')
        self.assertEqual(fields['n'], b'1')
        self.assertEqual(fields['quality'], b'high')
        images = [part.get_payload(decode=True) for part in parts if part.get_filename()]
        self.assertEqual(images, [b'SOURCE', b'REFERENCE'])
        self.assertTrue(url.endswith('/images/edits'))
        self.assertEqual(headers['Authorization'], 'Bearer dummy')
        self.assertNotIn(b'dummy', body)

    def test_google_inline_images_resolution_and_key_header(self):
        url, headers, body = make_request(GOOGLE, 'gemini-nano-banana-2.1', 'dummy', 'Prompt',
                                          1024, b'SOURCE', b'REFERENCE')
        payload = json.loads(body)
        self.assertEqual(payload['generationConfig']['imageConfig'], {'aspectRatio': '1:1', 'imageSize': '1K'})
        images = [base64.b64decode(part['inlineData']['data']) for part in payload['contents'][0]['parts'][1:]]
        self.assertEqual(images, [b'SOURCE', b'REFERENCE'])
        self.assertEqual(headers['x-goog-api-key'], 'dummy')
        self.assertNotIn('dummy', url)
        self.assertNotIn(b'dummy', body)

    def test_older_gemini_does_not_send_unsupported_image_size(self):
        _, _, body = make_request(GOOGLE, 'gemini-2.5-flash-image', 'dummy', 'Prompt', 1024, b'a', b'b')
        self.assertNotIn('imageSize', json.loads(body)['generationConfig']['imageConfig'])
        with self.assertRaises(ValueError):
            validate_options(GOOGLE, 'gemini-2.5-flash-image', 2048)

    def test_neighbor_images_follow_source_and_style_in_both_apis(self):
        for provider, model in ((OPENAI, 'gpt-image-2.5-sunburst'), (GOOGLE, 'gemini-nano-banana-2.1')):
            _, headers, body = make_request(provider, model, 'dummy', 'Prompt', 1024,
                                             b'SOURCE', b'STYLE', neighbors=(b'TOP', b'LEFT'))
            if provider == OPENAI:
                message = BytesParser(policy=policy.default).parsebytes(
                    ('Content-Type: ' + headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
                images = [part.get_payload(decode=True) for part in message.iter_parts() if part.get_filename()]
            else:
                images = [base64.b64decode(part['inlineData']['data'])
                          for part in json.loads(body)['contents'][0]['parts'][1:]]
            self.assertEqual(images, [b'SOURCE', b'STYLE', b'TOP', b'LEFT'])

    def test_profiles_and_compatible_size_reset(self):
        for provider, models in CAPABILITIES.items():
            for model, capability in models.items():
                for size in capability.sizes:
                    validate_options(provider, model, size)
                    self.assertEqual(compatible_size(provider, model, size), size)
                self.assertEqual(compatible_size(provider, model, 123), 1024)
        self.assertEqual(compatible_size(GOOGLE, 'gemini-2.5-flash-image', 4096), 1024)
        self.assertEqual(compatible_size(OPENAI, 'gpt-image-2', 4096), 1024)

    def test_overrides_require_explicit_unknown_size(self):
        for provider in (GOOGLE, OPENAI):
            selected = next(iter(CAPABILITIES[provider]))
            self.assertEqual(resolve_options(provider, selected, 1024), (selected, 1024))
            with self.assertRaisesRegex(ValueError, 'both'):
                resolve_options(provider, selected, 1024, 'future-image')
            self.assertEqual(resolve_options(provider, selected, 1024, 'future-image', '2048'),
                             ('future-image', 2048))
            for size in ('2K', '2048x2048', '1.5', '-1', '8193', '0', '1024.0'):
                with self.assertRaises(ValueError):
                    resolve_options(provider, selected, 1024, 'future-image', size)
        with self.assertRaises(ValueError):
            resolve_options(GOOGLE, 'gemini-2.5-flash-image', 1024, '', '2048')
        with self.assertRaises(ValueError):
            resolve_options(GOOGLE, 'gemini-nano-banana-2.1', 1024, '', '512')

    def test_openai_custom_square_limits_and_exact_request(self):
        for size in (1000, 800, 2896, 4096):
            with self.assertRaises(ValueError):
                validate_options(OPENAI, 'gpt-image-2', size)
        for size in (1024.0, True, None, '1024'):
            with self.assertRaises(ValueError):
                validate_options(OPENAI, 'gpt-image-2', size)
        model, size = resolve_options(OPENAI, 'gpt-image-2', 1024, '', '1536')
        _, _, body = make_request(OPENAI, model, 'dummy', 'Prompt', size, b'a', b'b')
        self.assertIn(b'1536x1536', body)

    def test_google_all_native_tokens_and_unknown_wire_format(self):
        for size, token in ((512, '512'), (1024, '1K'), (2048, '2K'), (4096, '4K')):
            for model in ('gemini-3.1-flash-image', 'future-image'):
                _, _, body = make_request(GOOGLE, model, 'dummy', 'Prompt', size, b'a', b'b')
                config = json.loads(body)['generationConfig']['imageConfig']
                self.assertEqual(config, {'aspectRatio': '1:1', 'imageSize': token})
        with self.assertRaises(ValueError):
            make_request(GOOGLE, 'future-image', 'dummy', 'Prompt', 1536, b'a', b'b')
        # A future model that resembles an old ID must not inherit its capability.
        _, _, body = make_request(GOOGLE, 'gemini-2.5-future', 'dummy', 'Prompt', 2048, b'a', b'b')
        self.assertEqual(json.loads(body)['generationConfig']['imageConfig']['imageSize'], '2K')

    def test_parse_openai_base64(self):
        self.assertEqual(parse_response(OPENAI, json.dumps({'data': [{'b64_json': 'aW1hZ2U='}]})), b'image')

    def test_parse_google_skips_internal_thought_images(self):
        payload = {'candidates': [{'content': {'parts': [
            {'thought': True, 'inlineData': {'mimeType': 'image/png', 'data': 'dGhvdWdodA=='}},
            {'inlineData': {'mimeType': 'image/png', 'data': 'aW1hZ2U='}},
        ]}}]}
        self.assertEqual(parse_response(GOOGLE, json.dumps(payload)), b'image')

    def test_google_snake_case_response(self):
        payload = {'candidates': [{'content': {'parts': [
            {'inline_data': {'mime_type': 'image/png', 'data': 'aW1hZ2U='}},
        ]}}]}
        self.assertEqual(parse_response(GOOGLE, json.dumps(payload)), b'image')

    def test_errors_are_actionable(self):
        for provider, payload, message in [
            (OPENAI, {'error': {'message': 'Quota exceeded'}}, 'Quota'),
            (GOOGLE, {'promptFeedback': {'blockReason': 'SAFETY'}}, 'SAFETY'),
            (OPENAI, {'data': [{'b64_json': '!!'}]}, 'corrupt'),
            (OPENAI, {'data': [{'url': 'https://untrusted.example'}]}, 'no image'),
        ]:
            with self.assertRaisesRegex(ValueError, message):
                parse_response(provider, json.dumps(payload))
        with self.assertRaises(ValueError):
            parse_response(OPENAI, '<html>proxy error</html>')

    def test_model_does_not_allow_url_injection(self):
        for model in ('../../models', 'foo?key=abc', 'foo/bar', ''):
            with self.assertRaises(ValueError):
                validate_options(GOOGLE, model, 1024)
        with self.assertRaises(ValueError):
            make_request(OPENAI, 'gpt-image-2.5-flare', 'key\r\nheader', 'Prompt', 1024, b'a', b'b')

    def test_redact_provider_errors(self):
        message = safe_error('key=example Bearer secret-dummy https://host?key=url-secret', 'example')
        for secret in ('example', 'secret-dummy', 'url-secret'):
            self.assertNotIn(secret, message)

    def test_deferred_authentication_has_no_placeholder_header(self):
        for provider, model in ((OPENAI, 'gpt-image-2'), (GOOGLE, 'gemini-3-pro-image')):
            url, headers, body = make_request(provider, model, None, 'Prompt', 1024, b'SOURCE', b'STYLE')
            self.assertNotIn('Authorization', headers)
            self.assertNotIn('x-goog-api-key', headers)
            self.assertTrue(url.startswith('https://'))
            self.assertTrue(body)
            with self.assertRaises(ValueError):
                make_request(provider, model, '', 'Prompt', 1024, b'SOURCE', b'STYLE')

    def test_key_validation_and_multiple_secret_redaction(self):
        for key in ('', ' ', '\nsecret', 'secret\r', 'two words', 'has\ttab'):
            with self.assertRaises(ValueError):
                validate_key(key)
        self.assertEqual(validate_key('  valid-key  '), 'valid-key')
        message = safe_error('openai-secret google-secret', ['openai-secret', 'google-secret'])
        self.assertNotIn('openai-secret', message)
        self.assertNotIn('google-secret', message)
        self.assertNotIn('other-secret', safe_error('x-goog-api-key: other-secret'))


if __name__ == '__main__':
    unittest.main()
