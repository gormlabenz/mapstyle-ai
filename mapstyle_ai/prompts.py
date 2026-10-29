# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Internal map instructions and explicit image roles, independent of QGIS."""
import re

PROMPT_VERSION = 1
DEFAULT_PROMPT = """Use the spatial arrangement from Image 1. Preserve positions, basic shapes and connections of buildings, roads, paths, water and land areas. Adapt details to the requested style without inventing additional buildings or paths. Keep map orientation, scale and crop unchanged. No labels, legend or border.
Return the complete image at exactly {tile_px} × {tile_px} pixels."""


def validate_style(user_prompt, reference_ids):
    if not reference_ids and not user_prompt.strip():
        raise ValueError('Add at least one reference image or describe your desired style.')
    mentioned = {int(value) for value in re.findall(r"\b(?:reference|referenz)\s+(\d+)\b", user_prompt, re.I)}
    missing = mentioned - set(reference_ids)
    if missing:
        names = ', '.join(f'Reference {number}' for number in sorted(missing))
        raise ValueError(f'{names} is not attached. Update your instructions or add the image again.')


def build_prompt(user_prompt, reference_ids, pixels):
    reference_ids = tuple(reference_ids)
    validate_style(user_prompt, reference_ids)
    lines = [DEFAULT_PROMPT.replace('{tile_px}', str(pixels)),
             'Image roles: Image 1 is the geographic map input.']
    for image_number, reference_id in enumerate(reference_ids, 2):
        lines.append(f'Image {image_number} = Reference {reference_id} (Referenz {reference_id}). '
                     'Use only its visual style, never its geography or objects.')
    if reference_ids:
        lines.append('Combine the shared visual characteristics of the references into one consistent style. '
                     'No reference has automatic priority. Specific user instructions refine or override '
                     'their visual style, while map geometry and connection requirements remain mandatory.')
    else:
        lines.append('There are no style reference images. Derive the visual style from the user instructions.')
    if user_prompt.strip():
        lines.extend(['User instructions (reference names above identify the attached images):', user_prompt])
    return '\n'.join(lines)
