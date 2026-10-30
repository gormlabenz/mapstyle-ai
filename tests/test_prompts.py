# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

import base64
from email import policy
from email.parser import BytesParser
import json
import unittest

from mapstyle_ai.prompts import build_prompt, validate_style
from mapstyle_ai.providers import GOOGLE, OPENAI, make_request, validate_image_count
from mapstyle_ai.seams import continuation_prompt


class PromptTests(unittest.TestCase):
    def test_text_only_keeps_user_text_literal_and_has_no_phantom_reference(self):
        text = 'Watercolor, pale blue water. Keep my literal {tile_px} example.'
        prompt = build_prompt(text, [], 1024)
        self.assertIn(text, prompt)
        self.assertIn('exactly 1024 × 1024', prompt)
        self.assertNotIn('Image 2', prompt)
        self.assertIn('There are no style reference images', prompt)

    def test_images_only_and_nonconsecutive_reference_names(self):
        prompt = build_prompt('', [1, 3], 2048)
        self.assertIn('Image 2 = Reference 1 (Referenz 1)', prompt)
        self.assertIn('Image 3 = Reference 3 (Referenz 3)', prompt)
        self.assertNotIn('User instructions', prompt)
        self.assertNotIn('Reference 2', prompt)
        self.assertNotIn('pencil', prompt)

    def test_no_style_and_removed_reference_mentions_are_rejected(self):
        for text, ids in [('', []), ('  \n', []), ('Colors like Reference 2', [1, 3]),
                          ('Linien wie referenz 2', [1])]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_style(text, ids)
        validate_style('Farben wie Referenz 3, lines like Reference 1.', [1, 3])

    def test_neighbor_strips_do_not_introduce_extra_image_roles(self):
        for ids in ([], [1], [1, 3, 4]):
            with self.subTest(ids=ids):
                base = build_prompt('Watercolor', ids, 1024)
                prompt = continuation_prompt(base, ['A1'], [(0, 0, 256, 1024)])
                self.assertIn('Connection from neighbor tile A1 at left', prompt)
                self.assertIn('already embedded in Image 1', prompt)
                self.assertNotIn(f'Image {2 + len(ids)}', prompt)
                self.assertIn('do not repeat, shift or expand', prompt)
                self.assertIn('width=256', prompt)
                start = continuation_prompt(base, [], [])
                self.assertNotIn('neighbor tile', start)
                self.assertNotIn('in the style of Image 2', start)

    def test_both_wire_formats_keep_source_references_and_neighbors_in_order(self):
        for provider, model in ((OPENAI, 'gpt-image-2.5-sunburst'),
                                (GOOGLE, 'gemini-nano-banana-2.1')):
            for references in ([], [b'ONE'], [b'ONE', b'THREE', b'FOUR']):
                with self.subTest(provider=provider, references=references):
                    prompt = build_prompt('Watercolor', [1, 3, 4][:len(references)], 1024)
                    _, headers, body = make_request(provider, model, 'dummy', prompt, 1024,
                                                   b'SOURCE', references, neighbors=[b'TOP', b'LEFT'])
                    if provider == OPENAI:
                        message = BytesParser(policy=policy.default).parsebytes(
                            ('Content-Type: ' + headers['Content-Type'] + '\r\n\r\n').encode() + body)
                        images = [part.get_payload(decode=True) for part in message.iter_parts()
                                  if part.get_filename()]
                    else:
                        images = [base64.b64decode(part['inlineData']['data'])
                                  for part in json.loads(body)['contents'][0]['parts'] if 'inlineData' in part]
                    self.assertEqual(images, [b'SOURCE', *references, b'TOP', b'LEFT'])

    def test_provider_limits_include_geographic_input_and_neighbors(self):
        validate_image_count(OPENAI, 'gpt-image-2.5-sunburst', 13, 2)
        with self.assertRaisesRegex(ValueError, '17'):
            validate_image_count(OPENAI, 'gpt-image-2.5-sunburst', 14, 2)
        validate_image_count(GOOGLE, 'gemini-nano-banana-2.1', 11, 2)
        with self.assertRaisesRegex(ValueError, '14 input'):
            validate_image_count(GOOGLE, 'gemini-nano-banana-2.1', 12, 2)
        with self.assertRaisesRegex(ValueError, '3 style'):
            validate_image_count(GOOGLE, 'gemini-3-pro-image', 4, 0)
        with self.assertRaisesRegex(ValueError, '20 MB'):
            make_request(GOOGLE, 'gemini-nano-banana-2.1', 'dummy', 'Style', 1024,
                         b'SOURCE', [b'x' * (16 * 1024 * 1024)])
