"""Audit text actually drawn, including visible ticks and annotations.

Axis locators may retain tick Text objects outside the view interval.  Such
objects can be visible=True but are not sent through Text.draw by Axis.draw.
Record a real canvas draw rather than treating every stored artist as output.
No artists, axis limits, data, or visibility flags are modified by this audit.
"""

def audit_rendered_text(fig, padding_pixels=2):
    from matplotlib.text import Text

    original_draw = Text.draw
    drawn = {}

    def record_draw(obj, renderer):
        result = original_draw(obj, renderer)
        if (obj.get_figure() is fig and obj.get_visible()
                and obj.get_text().strip()):
            drawn[id(obj)] = (obj, obj.get_window_extent(renderer).frozen())
        return result

    # Each worker renders serially in its own process. Restore the method even
    # if the renderer fails so this audit cannot affect subsequent exports.
    try:
        Text.draw = record_draw
        fig.canvas.draw()
    finally:
        Text.draw = original_draw

    width, height = fig.canvas.get_width_height()
    bad = []
    details = []
    for obj, box in drawn.values():
        if (box.width and box.height
                and (box.x0 < -padding_pixels or box.y0 < -padding_pixels
                     or box.x1 > width + padding_pixels
                     or box.y1 > height + padding_pixels)):
            bad.append(obj.get_text())
            details.append({
                'text': obj.get_text(),
                'bounds_pixels': [float(v) for v in box.extents],
            })

    # Record what the old all-artist scan would have reported, for diagnosis.
    renderer = fig.canvas.get_renderer()
    undrawn_outside = []
    for obj in fig.findobj(match=Text):
        if id(obj) in drawn or not obj.get_visible() or not obj.get_text().strip():
            continue
        box = obj.get_window_extent(renderer)
        if (box.width and box.height
                and (box.x0 < -padding_pixels or box.y0 < -padding_pixels
                     or box.x1 > width + padding_pixels
                     or box.y1 > height + padding_pixels)):
            undrawn_outside.append(obj.get_text())

    return {
        'audit_method': 'text_observed_during_canvas_draw_v2',
        'canvas_pixels': [int(width), int(height)],
        'padding_pixels': padding_pixels,
        'drawn_text_count': len(drawn),
        'text_outside_canvas': bad,
        'outside_text_details': details,
        'undrawn_stored_text_outside_canvas': undrawn_outside,
        'manual_visual_review_required': True,
    }
