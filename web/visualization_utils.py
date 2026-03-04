"""
Visualization and Styling Utilities

This module contains pure utility functions for color manipulation,
hex/RGB conversions, and visualization styling.

These functions have no PyScript/DOM dependencies.
"""


def hex_to_rgb(hex_color):
    """Convert hex color (#rrggbb) to (r, g, b) tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    """Convert (r, g, b) tuple to hex color #rrggbb."""
    return "#{:02x}{:02x}{:02x}".format(int(rgb[0]), int(rgb[1]), int(rgb[2]))


def lighten_color(hex_color, factor=0.7):
    """Lighten a hex color by blending with white.

    Args:
        hex_color: Hex color string like '#1b9e77'
        factor: Amount to blend with white (0=original, 1=white)
    """
    r, g, b = hex_to_rgb(hex_color)
    r = r + (255 - r) * factor
    g = g + (255 - g) * factor
    b = b + (255 - b) * factor
    return rgb_to_hex((r, g, b))


def get_line_weight(capacity_mw):
    """Calculate line weight based on transmission capacity.

    Args:
        capacity_mw: Transmission capacity in MW

    Returns:
        Line weight (pixel width) for map visualization (1-8 pixels)
    """
    # Scale: 1-8 pixels based on capacity
    # Typical range is ~100 MW to ~15000 MW
    min_weight = 1
    max_weight = 8
    min_cap = 100
    max_cap = 12000

    # Clamp and scale
    clamped = max(min_cap, min(max_cap, capacity_mw))
    normalized = (clamped - min_cap) / (max_cap - min_cap)
    return min_weight + normalized * (max_weight - min_weight)
