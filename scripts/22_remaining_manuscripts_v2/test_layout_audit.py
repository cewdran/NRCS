#!/usr/bin/env python3
"""Synthetic geometry regression checks only; no NRCS inputs or analyses."""
import json
import tempfile
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.text import Text
from layout_audit import audit_rendered_text


def main():
    checks = []

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        checks.append(label)

    fig, ax = plt.subplots(figsize=(4, 3))
    fig.subplots_adjust(left=.2, right=.9, bottom=.2, top=.9)
    ax.plot([0, 1], [0, 1])
    ax.set_xticks([0, .5, 1, 2], ['0', '0.5', '1', 'DORMANT_X'])
    ax.set_yticks([0, .5, 1, 2], ['0', '0.5', '1', 'DORMANT_Y'])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    result = audit_rendered_text(fig)
    check(not result['text_outside_canvas'], 'Undrawn out-of-view ticks do not fail')
    check(set(result['undrawn_stored_text_outside_canvas']) == {'DORMANT_X', 'DORMANT_Y'},
          'The old false-positive condition is reproduced and recorded')
    check(result['drawn_text_count'] >= 6, 'Visible tick labels are still audited')
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(4, 3))
    fig.text(1.1, .5, 'REAL_OVERFLOW')
    result = audit_rendered_text(fig)
    check('REAL_OVERFLOW' in result['text_outside_canvas'], 'Visible annotation overflow fails')
    with tempfile.TemporaryDirectory() as directory:
        from common import finish
        try:
            finish(fig, directory, 'synthetic_overflow', 'Synthetic fixture only.', plt)
        except RuntimeError as exc:
            check('REAL_OVERFLOW' in str(exc), 'Publication exporter rejects real overflow')
        else:
            raise AssertionError('Publication exporter must reject real overflow')
        check(not (Path(directory) / 'synthetic_overflow.pdf').exists(),
              'No publication PDF is written after a failed audit')
        check((Path(directory) / 'synthetic_overflow_DIAGNOSTIC.png').exists(),
              'A failed visible-text check leaves a diagnostic preview')
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(4, 3))
    fig.subplots_adjust(left=.2, right=.9, bottom=.2, top=.9)
    ax.set_xticks([0, 1], ['0', 'VISIBLE_TICK_' * 10])
    ax.set_xlim(0, 1)
    check(any('VISIBLE_TICK_' in text for text in audit_rendered_text(fig)['text_outside_canvas']),
          'An overflowing drawn tick label fails')
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(4, 3))
    ax.text(4, 4, 'HIDDEN_AXES_TEXT', transform=ax.transAxes)
    ax.set_visible(False)
    check(not audit_rendered_text(fig)['text_outside_canvas'], 'Hidden axes are not treated as drawn')
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(5, 4))
    fig.subplots_adjust(left=.25, right=.9, bottom=.25, top=.85)
    ax.plot([1, 10], [1, 10])
    ax.set_xscale('log')
    ax.set_yscale('symlog', linthresh=1)
    ax.invert_xaxis()
    ax.set_xlabel('Synthetic log axis')
    ax.set_ylabel('Synthetic symlog axis')
    check(not audit_rendered_text(fig)['text_outside_canvas'], 'Log, symlog and inverted axes render normally')
    plt.close(fig)

    fig = plt.figure()
    original_text_draw = Text.draw
    original_canvas_draw = fig.canvas.draw

    def render_failure():
        raise RuntimeError('Synthetic renderer failure')

    fig.canvas.draw = render_failure
    try:
        audit_rendered_text(fig)
    except RuntimeError:
        pass
    finally:
        fig.canvas.draw = original_canvas_draw
    check(Text.draw is original_text_draw, 'Text.draw is restored after a renderer exception')
    plt.close(fig)

    print(json.dumps({
        'status': 'PASS',
        'matplotlib_version': matplotlib.__version__,
        'checks': checks,
        'NRCS_data_loaded': False,
        'scientific_workers_executed': False,
        'Puma_execution_completed': False,
    }, indent=2))


if __name__ == '__main__':
    main()
