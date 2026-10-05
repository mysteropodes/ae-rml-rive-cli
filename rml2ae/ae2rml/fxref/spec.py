"""Reference renders of the AE-compatible WGSL effects: effect -> [(settings, held)], settings = {AE param index: value}.

held = 1: a setting kept out of view of whoever implements the effect, used only to verify it afterwards (its render
lives in ae_holdout/). Add an effect here, then `python render_refs.py <slug>` (After Effects open) renders its
references from src/src.png, and `python -m rml2ae.ae2rml.fxlib check <slug>` compares the WGSL with them.

    python spec.py        # rewrites renders.json (what fxlib check reads)"""
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
C = lambda r, g, b, a=1: [r, g, b, a]  # noqa: E731

SPEC = {
    "none": ("", [({}, 0)]),
    "fill": ("ADBE Fill", [({3: C(0.2, 0.6, 1)}, 0), ({3: C(1, 0.5, 0), 7: 0.5}, 0), ({3: C(0.9, 0.1, 0.5), 7: 0.8}, 1)]),
    "tint": ("ADBE Tint", [({3: 100}, 0), ({1: C(0.1, 0, 0.3), 2: C(1, 0.9, 0.5), 3: 100}, 0), ({1: C(0, 0.2, 0.2), 2: C(1, 1, 0.8), 3: 60}, 1)]),
    "exposure": ("ADBE Exposure2", [({3: 1}, 0), ({3: -1, 4: 0.05, 5: 1.5}, 0), ({3: 0.5, 5: 0.7}, 1)]),
    "invert": ("ADBE Invert", [({1: 1}, 0), ({1: 7}, 0), ({1: 2}, 0), ({1: 1, 2: 40}, 1)]),
    "levels": ("ADBE Easy Levels2", [({3: 0.2, 4: 0.8}, 0), ({5: 2.0}, 0), ({6: 0.1, 7: 0.9, 5: 0.6}, 0), ({3: 0.1, 4: 0.9, 5: 1.4}, 1)]),
    "color_balance_hls": ("ADBE Color Balance (HLS)", [({1: 90}, 0), ({2: 30, 3: -50}, 0), ({1: -45, 3: 40}, 1)]),
    "hue_saturation": ("ADBE HUE SATURATION", [({3: 120}, 0), ({4: -60, 5: 20}, 0), ({6: 1, 7: 200, 8: 60, 9: 0}, 0), ({3: -60, 4: 40}, 1)]),
    "brightness_contrast": ("ADBE Brightness & Contrast 2", [({1: 40}, 0), ({2: 50}, 0), ({1: -30, 2: -40}, 0), ({1: 20, 2: 30}, 1)]),
    "tritone": ("ADBE Tritone", [({}, 0), ({1: C(1, 0.9, 0.7), 2: C(0.2, 0.5, 0.6), 3: C(0.05, 0, 0.1)}, 0), ({4: 30}, 1)]),
    "shift_channels": ("ADBE Shift Channels", [({2: 3, 3: 4, 4: 2}, 0), ({1: 5}, 0), ({2: 9, 4: 10}, 1)]),
    "luma_key": ("ADBE Luma Key", [({1: 2, 2: 100, 3: 20}, 0), ({1: 1, 2: 60, 5: 10}, 0), ({1: 2, 2: 150, 3: 40, 5: 5}, 1)]),
    "posterize": ("ADBE Posterize", [({1: 4}, 0), ({1: 2}, 0), ({1: 6}, 1)]),
    "black_white": ("ADBE Black&White", [({}, 0), ({1: 100, 5: -50, 7: 1}, 0), ({2: 120, 4: 0}, 1)]),
    "gaussian_blur": ("ADBE Gaussian Blur 2", [({1: 10, 3: 0}, 0), ({1: 30, 3: 0}, 0), ({1: 20, 2: 2, 3: 0}, 0), ({1: 20, 3: 1}, 0), ({1: 15, 3: 0}, 1)]),
    "gaussian_blur_legacy": ("ADBE Gaussian Blur", [({1: 10}, 0), ({1: 25}, 0), ({1: 16}, 1)]),
    "fast_blur_legacy": ("ADBE Fast Blur", [({1: 10, 3: 0}, 0), ({1: 25, 3: 0}, 0), ({1: 16, 3: 0}, 1)]),
    "box_blur": ("ADBE Box Blur2", [({1: 8, 2: 1, 4: 0}, 0), ({1: 8, 2: 3, 4: 0}, 0), ({1: 12, 2: 2, 4: 0}, 1)]),
    "directional_blur": ("ADBE Motion Blur", [({1: 0, 2: 20}, 0), ({1: 45, 2: 30}, 0), ({1: 120, 2: 15}, 1)]),
    "cc_radial_fast_blur": ("CC Radial Fast Blur", [({2: 50}, 0), ({2: 80, 3: 2}, 0), ({1: [200, 120], 2: 60, 3: 3}, 0), ({2: 30, 1: [400, 200]}, 1)]),
    "cc_radial_blur": ("CC Radial Blur", [({1: 1, 2: 30}, 0), ({1: 4, 2: 20}, 0), ({1: 6, 2: 40}, 0), ({1: 3, 2: 25}, 1)]),
    # 3..7: how blur width, threshold and ramp change with the choke (choke 6, held out, is off by up to 119 levels);
    # 7 = View Matte (the alpha alone, as grey) at choke 10
    "simple_choker": ("ADBE Simple Choker", [({2: 3}, 0), ({2: -4}, 0), ({2: 6}, 1), ({2: 1}, 0), ({2: 10}, 0),
                                             ({2: 20}, 0), ({2: -10}, 0), ({1: 2, 2: 10}, 0)]),
    "minimax": ("ADBE Minimax", [({1: 1, 2: 4}, 0), ({1: 2, 2: 4}, 0), ({1: 2, 2: 6, 3: 2}, 0), ({1: 3, 2: 3}, 1)]),
    "mosaic": ("ADBE Mosaic", [({1: 20, 2: 10}, 0), ({1: 40, 2: 20, 3: 1}, 0), ({1: 13, 2: 7}, 1)]),
    "motion_tile": ("ADBE Tile", [({2: 50, 3: 50}, 0), ({2: 50, 3: 50, 6: 1}, 0), ({2: 40, 3: 60, 7: 30, 8: 1}, 0), ({1: [200, 150], 2: 60, 3: 60, 6: 1}, 1)]),
    "displacement_map": ("ADBE Displacement Map", [({3: 20, 5: 20}, 0), ({2: 1, 4: 2, 3: 30, 5: -15}, 0), ({2: 5, 4: 5, 3: 12, 5: 12}, 1)]),
    "turbulent_displace": ("ADBE Turbulent Displace", [({}, 0), ({2: 80, 3: 50}, 0), ({2: 30, 3: 150, 5: 3}, 1)]),
    "optics_compensation": ("ADBE Optics Compensation", [({1: 60}, 0), ({1: 60, 2: 1}, 0), ({1: 90}, 1)]),
    "corner_pin": ("ADBE Corner Pin", [({1: [50, 30], 2: [600, 0], 3: [0, 360], 4: [560, 330]}, 0), ({1: [0, 40], 2: [640, 0], 3: [80, 360], 4: [600, 300]}, 1)]),
    "magnify": ("ADBE Magnify", [({2: [200, 150], 3: 200, 5: 120}, 0), ({1: 2, 2: [400, 200], 3: 300, 5: 80, 6: 10}, 0), ({2: [300, 180], 3: 150, 5: 100}, 1)]),
    "glow": ("ADBE Glo2", [({3: 20}, 0), ({2: 100, 3: 40, 4: 2}, 0), ({7: 2, 3: 25, 12: C(1, 0.5, 0), 13: C(0, 0, 1)}, 0), ({2: 60, 3: 15, 4: 1.5}, 1)]),
    "drop_shadow": ("ADBE Drop Shadow", [({4: 15, 5: 10}, 0), ({1: C(0.3, 0, 0.5), 2: 200, 3: 45, 4: 25, 5: 30}, 0), ({6: 1, 4: 10, 5: 5}, 0), ({3: 200, 4: 12, 5: 20, 2: 180}, 1)]),
    "emboss": ("ADBE Emboss", [({}, 0), ({1: 120, 2: 3, 3: 200}, 0), ({1: 30, 2: 2, 3: 150, 4: 30}, 1)]),
    "checkerboard": ("ADBE Checkerboard", [({}, 0), ({2: 2, 4: 30}, 0), ({2: 3, 4: 40, 5: 20, 10: C(1, 0, 0), 11: 50}, 0), ({2: 2, 4: 24, 1: [100, 50]}, 1)]),
    "four_color_gradient": ("ADBE 4ColorGradient", [({}, 0), ({11: 50, 13: 70}, 0), ({2: [100, 100], 4: [540, 60], 11: 200}, 1)]),
    "gradient_ramp": ("ADBE Ramp", [({}, 0), ({1: [100, 100], 3: [540, 260]}, 0), ({5: 2, 1: [320, 180], 3: [320, 0]}, 0), ({1: [0, 0], 3: [640, 360], 7: 0.3}, 1)]),
    "linear_wipe": ("ADBE Linear Wipe", [({1: 40}, 0), ({1: 50, 2: 30, 3: 40}, 0), ({1: 60, 2: 200, 3: 20}, 1)]),
    "cc_scale_wipe": ("CC Scale Wipe", [({1: 40}, 0), ({1: -30, 3: 120}, 0), ({1: 60, 2: [200, 200]}, 1)]),
    # lot 1 (unverified until rendered): visible settings only — held-out ones are to be added by someone who has not
    # read the shaders (held = 1), so that they stay an independent check
    "channel_mixer": ("ADBE CHANNEL MIXER", [({1: 50, 2: 50, 6: 80, 12: 20}, 0), ({13: 1, 1: 30, 2: 59, 3: 11}, 0),
                                             ({5: -50, 9: 120, 4: 10}, 0)]),
    "set_channels": ("ADBE Set Channels", [({2: 2, 4: 3, 6: 1}, 0), ({2: 5, 4: 6, 6: 7, 8: 9}, 0), ({2: 8, 4: 10, 6: 4}, 0)]),
    "offset": ("ADBE Offset", [({1: [400, 250]}, 0), ({1: [100.5, 40.25], 2: 0.3}, 0)]),
    "radial_wipe": ("ADBE Radial Wipe", [({1: 30}, 0), ({1: 50, 2: 45, 4: 2, 5: 20}, 0), ({1: 40, 3: [200, 120], 4: 3}, 0)]),
    "venetian_blinds": ("ADBE Venetian Blinds", [({1: 40}, 0), ({1: 60, 2: 30, 3: 25, 4: 5}, 0), ({1: 50, 2: 90, 3: 16}, 0)]),
    "photo_filter": ("ADBE Photo Filter", [({}, 0), ({1: 12, 3: 60}, 0), ({1: 4, 3: 80, 4: 0}, 0)]),
    "vibrance": ("ADBE Vibrance", [({1: 60}, 0), ({1: -50}, 0), ({1: 30, 2: 40}, 0), ({2: -80}, 0)]),
    # lot 2 (unverified until rendered): visible settings only; held-out ones are to be added independently
    "threshold": ("ADBE Threshold2", [({1: 0.5}, 0), ({1: 0.235}, 0), ({1: 0.784}, 0)]),
    "gamma_pedestal_gain": ("ADBE Gamma/Pedestal/Gain2", [({2: 0.6, 5: 1.4, 8: 1.0}, 0), ({3: 0.1, 4: 0.9, 10: 0.7}, 0), ({1: 2.0}, 0)]),
    "leave_color": ("ADBE Leave Color", [({1: 100, 2: [1, 0, 0, 1], 3: 30, 4: 10}, 0), ({1: 100, 2: [0, 0.3, 1, 1], 3: 15, 4: 20, 5: 2}, 0), ({1: 60, 2: [1, 1, 0, 1], 3: 40}, 0)]),
    "sharpen": ("ADBE Sharpen", [({1: 20}, 0), ({1: 60}, 0)]),
    "find_edges": ("ADBE Find Edges", [({}, 0), ({1: 1}, 0), ({2: 0.5}, 0)]),
    "unsharp_mask": ("ADBE Unsharp Mask2", [({2: 100, 3: 3}, 0), ({2: 200, 3: 8, 4: 0.04}, 0)]),
    "mirror": ("ADBE Mirror", [({1: [320, 180]}, 0), ({1: [200, 150], 2: 30}, 0), ({1: [320, 100], 2: 90}, 0)]),
    "polar_coordinates": ("ADBE Polar Coordinates", [({1: 1, 2: 1}, 0), ({1: 1, 2: 2}, 0), ({1: 0.5, 2: 1}, 0)]),
    "twirl": ("ADBE Twirl", [({1: 90}, 0), ({1: -200, 2: 60}, 0), ({1: 45, 3: [200, 120], 2: 40}, 0)]),
    "bulge": ("ADBE Bulge", [({1: 120, 2: 120}, 0), ({1: 150, 2: 80, 4: -0.8}, 0), ({1: 100, 2: 100, 3: [200, 120], 4: 2}, 0)]),
    "spherize": ("ADBE Spherize", [({1: 150}, 0), ({1: 100, 2: [220, 140]}, 0)]),
    "radial_blur": ("ADBE Radial Blur", [({2: 20}, 0), ({2: 30, 4: 2}, 0), ({2: 15, 3: [200, 120]}, 0)]),
    "cc_vignette": ("CS Vignette", [({1: 80}, 0), ({1: 100, 2: 120}, 0), ({1: 60, 3: [200, 120]}, 0)]),
    "iris_wipe": ("ADBE IRIS_WIPE", [({3: 120}, 0), ({2: 10, 3: 150, 4: 1, 5: 60, 6: 20}, 0), ({1: [200, 140], 2: 8, 3: 100, 7: 15}, 0)]),
    # lot 3 (unverified until rendered)
    "color_balance": ("ADBE Color Balance 2", [({1: 40, 6: -30, 9: 50}, 0), ({4: 60, 5: -20, 10: 1}, 0)]),
    "change_color": ("ADBE Change Color", [({2: 120, 5: [1, 0, 0, 1], 6: 30, 7: 10}, 0), ({1: 2, 5: [0, 0.4, 1, 1], 6: 20, 7: 20, 8: 2}, 0), ({2: -60, 3: 20, 5: [1, 1, 0, 1], 6: 40}, 0)]),
    "cc_toner": ("CC Toner", [({}, 0), ({1: 1, 2: [1, 0.9, 0.6, 1], 6: [0.1, 0, 0.3, 1]}, 0), ({1: 3, 7: 0.3}, 0)]),
    "cc_color_offset": ("CC Color Offset", [({1: 90, 2: 180, 3: 270}, 0), ({1: 120, 4: 2}, 0), ({2: 200, 4: 3}, 0)]),
    "arithmetic": ("ADBE Arithmetic", [({1: 3, 2: 128, 3: 64, 4: 200}, 0), ({1: 4, 2: 100, 3: 100, 4: 100, 5: 0}, 0), ({1: 11, 2: 128, 3: 128, 4: 128}, 0)]),
    "remove_color_matting": ("ADBE Remove Color Matting", [({1: [1, 1, 1, 1]}, 0), ({1: [0.5, 0.5, 0.5, 1]}, 0)]),
    "color_key": ("ADBE Color Key", [({1: [1, 0.2, 0.2, 1], 2: 60}, 0), ({1: [0, 1, 0, 1], 2: 120, 4: 3}, 0)]),
    "extract": ("ADBE Extract", [({3: 60, 4: 200}, 0), ({2: 2, 3: 100, 5: 40}, 0), ({3: 80, 4: 180, 7: 1}, 0)]),
    "spill_suppressor": ("ADBE Spill Suppressor", [({1: [0, 1, 0, 1]}, 0), ({1: [0, 0, 1, 1], 2: 60}, 0)]),
    "cc_threshold": ("CC Threshold", [({}, 0), ({1: 0.31, 2: 2, 3: 1}, 0), ({1: 0.63, 4: 0.4}, 0)]),
    "cc_threshold_rgb": ("CC Threshold RGB", [({}, 0), ({1: 0.25, 2: 0.8, 3: 0.4, 5: 1}, 0)]),
    "median": ("ADBE Median", [({1: 3}, 0), ({1: 8, 2: 1}, 0)]),
    # lot 4 (unverified until rendered)
    "wave_warp": ("ADBE Wave Warp", [({}, 0), ({1: 3, 2: 20, 3: 80, 4: 30}, 0), ({1: 2, 2: 6, 3: 25, 7: 90}, 0)]),
    "ripple": ("ADBE Ripple", [({}, 0), ({1: 80, 5: 40, 6: 10, 7: 90}, 0), ({2: [200, 120], 5: 12, 6: 6}, 0)]),
    "cc_tiler": ("CC Tiler", [({1: 0.25}, 0), ({1: 0.5, 2: [200, 120]}, 0), ({1: 0.33, 3: 0.4}, 0)]),
    "grid": ("ADBE Grid", [({}, 0), ({2: 3, 4: 60, 5: 30, 6: 4, 12: [1, 0, 0, 1], 14: 2}, 0)]),
    "ellipse": ("ADBE ELLIPSE", [({}, 0), ({2: 400, 3: 200, 4: 40, 5: 0.5, 6: [1, 0.8, 0, 1], 7: [1, 0, 0.4, 1], 8: 1}, 0)]),
    "bevel_alpha": ("ADBE Bevel Alpha", [({}, 0), ({1: 6, 2: 45, 4: 0.8}, 0)]),
    "radial_shadow": ("ADBE Radial Shadow", [({3: [100, 50], 4: 20}, 0), ({1: [0.4, 0, 0.6, 1], 2: 0.8, 3: [500, 300], 4: 40, 8: 1}, 0)]),
    "channel_blur": ("ADBE Channel Blur", [({1: 20}, 0), ({1: 5, 2: 15, 3: 30, 6: 2}, 0), ({4: 12, 5: 1}, 0)]),
    "cc_cross_blur": ("CS CrossBlur", [({1: 20, 2: 20}, 0), ({1: 40, 2: 5, 4: 1}, 0)]),
    "bilateral_blur": ("ADBE Bilateral", [({}, 0), ({1: 12, 2: 40}, 0)]),
    # lot 5 (unverified until rendered; random effects: amount and scale only, AE's generator is not public)
    "gradient_wipe": ("ADBE Gradient Wipe", [({1: 0.4}, 0), ({1: 0.5, 2: 0.3}, 0), ({1: 0.3, 5: 1}, 0)]),
    "block_dissolve": ("ADBE Block Dissolve", [({1: 40, 2: 20, 3: 20}, 0), ({1: 70, 2: 8, 3: 40}, 0)]),
    "circle": ("ADBE Circle", [({}, 0), ({1: [200, 120], 2: 100, 10: [1, 0.5, 0, 1], 12: 2}, 0)]),
    "cc_light_rays": ("CC Light Rays", [({}, 0), ({1: 200, 2: [500, 100], 3: 70}, 0)]),
    "cc_light_burst": ("CC Light Burst 2.5", [({}, 0), ({1: [200, 120], 3: 80, 4: 1}, 0)]),
    "cc_spotlight": ("CC Spotlight", [({}, 0), ({1: [100, 50], 2: [400, 220], 4: 40, 5: 0.5, 6: [1, 0.8, 0.4, 1], 8: 2}, 0)]),
    "noise": ("ADBE Noise", [({1: 30}, 0), ({1: 60, 2: 0}, 0)]),
    "scatter": ("ADBE Scatter", [({1: 5}, 0), ({1: 12, 2: 2}, 0)]),
    "broadcast_colors": ("ADBE Broadcast Colors", [({3: 90}, 0), ({2: 2, 3: 100}, 0), ({1: 2, 2: 3, 3: 95}, 0)]),
    # lot 6 (unverified until rendered)
    "cc_power_pin": ("CC Power Pin", [({1: [60, 20], 2: [600, 40], 3: [20, 340], 4: [620, 300]}, 0), ({1: [0, 60], 2: [640, 0], 3: [100, 360], 4: [560, 330]}, 0)]),
    "cc_radial_scalewipe": ("CC Radial ScaleWipe", [({1: 0.3}, 0), ({1: 0.5, 2: [200, 120]}, 0), ({1: 0.4, 3: 1}, 0)]),
    "cc_slant": ("CC Slant", [({1: 30}, 0), ({1: -20, 3: 60, 4: [320, 300], 5: 1, 6: [0.2, 0.2, 0.2, 1]}, 0)]),
    "cc_split": ("CC Split", [({3: 60}, 0), ({1: [100, 60], 2: [500, 300], 3: 100}, 0)]),
    "cc_lens": ("CC Lens", [({}, 0), ({2: 80, 3: 50}, 0), ({1: [200, 120], 3: -40}, 0)]),
    "cc_kaleida": ("CC Kaleida", [({2: 50}, 0), ({1: [200, 120], 2: 30, 4: 25}, 0)]),
    "solid_composite": ("ADBE Solid Composite", [({2: [0.2, 0.4, 0.8, 1]}, 0), ({1: 50, 2: [1, 0.8, 0, 1], 3: 70}, 0)]),
    "dust_scratches": ("ADBE Dust & Scratches", [({1: 3, 2: 20 / 255}, 0), ({1: 6, 2: 5 / 255}, 0)]),
    "noise_alpha": ("ADBE Noise Alpha", [({2: 50}, 0), ({1: 2, 2: 80, 3: 3}, 0)]),
    "noise_hls": ("ADBE Noise HLS2", [({2: 20, 3: 20, 4: 20}, 0), ({1: 3, 3: 40, 5: 4}, 0)]),
    # lot 7 (unverified until rendered)
    "cc_light_sweep": ("CC Light Sweep", [({}, 0), ({1: [200, 120], 2: 20, 3: 2, 4: 120, 5: 80, 8: [1, 0.8, 0.3, 1]}, 0), ({3: 3, 9: 3, 4: 80}, 0)]),
    "beam": ("ADBE Laser", [({4: 0.7}, 0), ({1: [100, 300], 2: [550, 60], 3: 0.6, 4: 0.9, 7: 0.3, 9: [1, 0, 0, 1], 11: 1}, 0)]),
    "cc_jaws": ("CC Jaws", [({1: 0.2}, 0), ({1: 0.35, 3: 90, 4: 0.5, 5: 20}, 0)]),
    "cc_line_sweep": ("CS LineSweep", [({1: 40}, 0), ({1: 60, 2: 45, 3: 12, 4: 20}, 0)]),
    "cc_light_wipe": ("CC Light Wipe", [({1: 20}, 0), ({1: 30, 4: 3, 2: [200, 120]}, 0)]),
    "bevel_edges": ("ADBE Bevel Edges", [({}, 0), ({1: 0.25, 2: 45, 4: 0.8, 3: [1, 0.9, 0.6, 1]}, 0)]),
    "linear_color_key": ("ADBE Linear Color Key2", [({3: [1, 0, 0, 1], 5: 20, 6: 10}, 0), ({3: [0, 0.6, 1, 1], 4: 2, 5: 10, 6: 30}, 0), ({3: [1, 1, 0, 1], 7: 2, 5: 25}, 0)]),
    "smart_blur": ("ADBE Smart Blur", [({1: 6, 2: 40}, 0), ({1: 4, 2: 20, 3: 2}, 0), ({1: 8, 2: 60, 3: 3}, 0)]),
    "reduce_interlace_flicker": ("ADBE Reduce Interlace Flicker", [({1: 2}, 0), ({1: 8}, 0)]),
    "advanced_spill_suppressor": ("ADBE Spill2", [({}, 0), ({2: 50}, 0)]),
    "color_emboss": ("ADBE Color Emboss", [({}, 0), ({1: 120, 2: 3, 3: 200}, 0)]),
    # lot 8 (unverified until rendered)
    "levels_individual": ("ADBE Pro Levels2", [({11: 0.2, 25: 0.1, 27: 0.7}, 0), ({4: 0.1, 5: 0.9, 20: 1.6}, 0), ({14: 0.2, 29: 0.8, 35: 0.6}, 0)]),
    "change_to_color": ("ADBE Change To Color", [({1: [1, 0, 0, 1], 2: [0, 0.4, 1, 1], 6: 0.3}, 0), ({1: [1, 1, 0, 1], 2: [1, 0, 1, 1], 3: 4, 6: 0.4, 10: 0.3}, 0), ({1: [0, 0.3, 1, 1], 2: [1, 0.5, 0, 1], 4: 2, 6: 0.5}, 0)]),
    "channel_combiner": ("ADBE Channel Combiner", [({5: 1}, 0), ({5: 5, 6: 3}, 0), ({5: 6, 6: 4, 7: 1}, 0), ({5: 3, 8: 1}, 0)]),
    "color_range": ("ADBE Color Range", [({3: 3, 4: 150, 5: 255, 6: 0, 7: 120, 8: 0, 9: 120}, 0), ({3: 1, 4: 0, 5: 255, 6: 140, 7: 255, 8: 0, 9: 255, 2: 20}, 0), ({3: 2, 4: 100, 5: 255, 6: 0, 7: 255, 8: 0, 9: 128, 2: 40}, 0)]),
    "matte_choker": ("ADBE Matte Choker", [({}, 0), ({1: 8, 2: -40, 3: 0.3}, 0), ({1: 2, 2: 20, 4: 6, 5: 30, 6: 0.2}, 0)]),
    "cc_kernel": ("CS Kernel", [({2: 1, 3: 1, 4: 1, 7: 1, 9: 1, 12: 1, 13: 1, 14: 1, 16: 9}, 0), ({3: -1, 7: -1, 8: 5, 9: -1, 13: -1}, 0), ({2: -1, 4: 1, 7: -2, 8: 0, 9: 2, 12: -1, 14: 1, 16: 1, 17: 1}, 0)]),
    "cc_vector_blur": ("CC Vector Blur", [({2: 20}, 0), ({1: 2, 2: 15, 3: 45}, 0), ({1: 3, 2: 12, 6: 4, 7: 20}, 0)]),
    "cc_bend_it": ("CC Bend It", [({1: 40}, 0), ({1: -60, 2: [100, 180], 3: [540, 180], 4: 2}, 0), ({1: 90, 4: 3}, 0)]),
    "cc_griddler": ("CC Griddler", [({1: 150, 2: 150, 3: 15, 5: 0}, 0), ({3: 20, 4: 30, 5: 0}, 0), ({1: 60, 2: 120, 3: 8, 5: 1}, 0)]),
    "cc_simple_wire_removal": ("CC Simple Wire Removal", [({4: 20}, 0), ({1: [0, 180], 2: [640, 200], 3: 3, 4: 16, 5: 0.5}, 0), ({3: 4, 4: 24, 6: 0.5}, 0)]),
    "lens_flare": ("ADBE Lens Flare", [({}, 0), ({1: [500, 100], 2: 140, 3: 2}, 0), ({1: [200, 250], 3: 3, 4: 30}, 0)]),
    "cc_grid_wipe": ("CC Grid Wipe", [({1: 0.4}, 0), ({1: 0.5, 3: 30, 6: 2, 5: 8}, 0), ({1: 0.6, 6: 3, 4: 120, 2: [100, 80], 7: 1}, 0)]),
    "cc_block_load": ("CS BlockLoad", [({1: 30}, 0), ({1: 65, 4: 1}, 0), ({1: 10, 3: 0}, 0)]),
    # lot 9: effects that read a second layer (unverified until rendered; the map footage is layer 2, NEEDS_MAP)
    "blend": ("ADBE Blend", [({3: 0}, 0), ({2: 2, 3: 0}, 0), ({2: 4, 3: 0.3}, 0), ({2: 5, 3: 0}, 0)]),
    "calculations": ("ADBE Calculations", [({8: 0.5}, 0), ({8: 1, 12: 4}, 0), ({2: 2, 12: 8, 8: 0.8}, 0), ({7: 3, 8: 1, 12: 18}, 0)]),
    "compound_arithmetic": ("ADBE Compound Arithmetic", [({2: 2}, 0), ({2: 5}, 0), ({2: 3, 4: 3}, 0), ({2: 8, 4: 2, 6: 25}, 0)]),
    "difference_matte": ("ADBE Difference Matte2", [({}, 0), ({4: 25, 5: 10}, 0), ({1: 3, 4: 15, 5: 20}, 0)]),
    "texturize": ("ADBE Texturize", [({}, 0), ({2: 45, 3: 2}, 0)]),
    "compound_blur": ("ADBE Compound Blur", [({}, 0), ({2: 10, 4: 1}, 0)]),
    "cc_image_wipe": ("CC Image Wipe", [({1: 0.4}, 0), ({1: 0.6, 2: 0.2, 8: 1}, 0)]),
    "three_d_glasses": ("ADBE 3D Glasses2", [({3: 12, 7: 3}, 0), ({7: 4, 3: -8}, 0), ({7: 1}, 0), ({7: 5, 8: 5, 3: 6}, 0)]),
    # lot 10: distort (unverified until rendered)
    "cc_bender": ("CC Bender", [({1: 40}, 0), ({1: -60, 2: 2}, 0), ({1: 50, 2: 3, 4: [100, 180], 5: [540, 180]}, 0)]),
    "cc_split2": ("CC Split 2", [({3: 40, 4: 120}, 0), ({1: [0, 180], 2: [640, 200], 3: 100, 4: 10}, 0)]),
    "cc_smear": ("CC Smear", [({}, 0), ({1: [200, 100], 2: [420, 260], 3: 70, 4: 80}, 0)]),
    "cc_ripple_pulse": ("CC Ripple Pulse", [({2: 25}, 0), ({2: 40, 4: 40, 1: [200, 120]}, 0)]),
    "cc_flo_motion": ("CC Flo Motion", [({3: 60, 5: -40, 6: 0}, 0), ({3: -80, 5: 80, 6: 1, 8: 3}, 0)]),
    "cc_twister": ("CC Twister", [({1: 0.5}, 0), ({1: 0.8, 5: 90, 3: 0}, 0)]),
    "cc_page_turn": ("CC Page Turn", [({}, 0), ({2: [400, 200], 3: 100, 4: 40, 8: 30}, 0), ({6: 2}, 0)]),
    "cc_sphere": ("CC Sphere", [({}, 0), ({3: 60, 2: 20, 7: 150}, 0), ({9: 3, 4: 30}, 0)]),
    "cc_cylinder": ("CC Cylinder", [({}, 0), ({1: 60, 9: 90}, 0), ({13: 3, 9: 45}, 0)]),
    "warp": ("ADBE WRPMESH", [({1: 1}, 0), ({1: 5, 3: 60}, 0), ({1: 8, 3: 40, 2: 2}, 0), ({1: 15, 3: 70}, 0), ({1: 4, 3: -50, 4: 30}, 0)]),
    "bezier_warp": ("ADBE BEZMESH", [({2: [213.3, 80], 3: [426.7, -80], 8: [426.7, 280], 9: [213.3, 440]}, 0), ({1: [60, 40], 4: [600, 0], 7: [640, 320], 10: [20, 360], 5: [560, 120], 11: [80, 240]}, 0)]),
    # lot 11: colour, keying, stylize (unverified until rendered)
    "colorama": ("APC Colorama", [({}, 0), ({2: 5, 6: 90, 11: 2}, 0), ({15: 2, 30: 30}, 0)]),
    "selective_color": ("ADBE SelectiveColor", [({9: -50, 11: 30}, 0), ({1: 2, 21: 40, 22: -30, 51: 20}, 0), ({45: -40, 46: 20}, 0)]),
    "shadow_highlight": ("ADBE ShadowHighlight", [({}, 0), ({1: 0, 2: 80, 3: 40}, 0), ({1: 0, 2: 30, 7: 80, 8: 10}, 0)]),
    "cc_color_neutralizer": ("CS Color Neutralizer", [({7: [0.6, 0.45, 0.4, 1]}, 0), ({1: [0.05, 0.0, 0.15, 1], 13: [1, 0.95, 0.8, 1], 9: 20, 17: -30, 20: 25}, 0)]),
    "color_difference_key": ("ADBE Color Difference Key", [({3: [0, 0.3, 1, 1], 5: 60, 6: 140}, 0), ({3: [1, 0, 0, 1], 2: 7, 15: 40, 16: 200}, 0)]),
    "eyedropper_fill": ("ADBE Eyedropper Fill", [({1: [100, 60], 2: 20}, 0), ({1: [500, 280], 2: 40, 4: 1, 5: 0.3}, 0)]),
    "cartoon": ("ADBE Cartoonify", [({}, 0), ({1: 1, 5: 4, 6: 0.01}, 0), ({1: 2, 9: 1.2}, 0)]),
    "cc_glass": ("CC Glass", [({3: 5}, 0), ({3: 5, 4: 20, 5: 100, 6: 300}, 0)]),
    "cc_hextile": ("CS HexTile", [({}, 0), ({2: 30, 5: 30, 6: 50}, 0)]),
    "cc_repetile": ("CC RepeTile", [({1: 200, 3: 100}, 0), ({1: 100, 2: 100, 3: 100, 4: 100, 5: 5}, 0)]),
    "cc_burn_film": ("CC Burn Film", [({1: 30}, 0), ({1: 60, 2: [150, 100], 3: 7}, 0)]),
    "camera_lens_blur": ("ADBE Camera Lens Blur", [({1: 10}, 0), ({1: 16, 3: 1, 6: 20, 17: 30, 18: 0.78}, 0), ({1: 8, 4: 100, 5: 2}, 0)]),
    # lot 12: noises, generators, stylize (unverified until rendered)
    "fractal_noise": ("ADBE Fractal Noise", [({}, 0), ({1: 2, 4: 200, 10: 50}, 0), ({2: 1, 16: 3, 31: 4}, 0), ({1: 3, 24: 90, 31: 5, 30: 60}, 0)]),
    "turbulent_noise": ("ADBE AIF Perlin Noise 3D", [({}, 0), ({1: 2, 4: 200, 10: 50}, 0), ({2: 1, 16: 3, 27: 4}, 0), ({1: 3, 21: 90, 27: 5, 26: 60}, 0)]),
    "cell_pattern": ("ADBE Cell Pattern", [({}, 0), ({1: 2, 6: 40}, 0), ({1: 5, 3: 150, 17: 3}, 0), ({1: 12, 6: 80}, 0)]),
    "roughen_edges": ("ADBE Roughen Edges", [({}, 0), ({1: 2, 3: 20, 6: 60}, 0), ({1: 3, 3: 12, 4: 3}, 0)]),
    "fractal": ("ADBE Fractal", [({}, 0), ({4: -0.745, 5: 0.11, 6: 6, 7: 300}, 0), ({1: 5, 4: -0.8, 5: 0.156}, 0)]),
    "cc_plastic": ("CC Plastic", [({3: 5}, 0), ({3: 5, 4: 20, 5: 100, 23: 80, 25: 0}, 0)]),
    "cc_blobbylize": ("CC Blobbylize", [({4: 12}, 0), ({4: 20, 5: 30, 3: 5}, 0)]),
    "cc_glass_wipe": ("CC Glass Wipe", [({1: 40}, 0), ({1: 70, 4: 30, 5: 40}, 0)]),
    "cc_threads": ("CS Threads", [({}, 0), ({1: 24, 2: 24, 4: 30, 6: 70, 8: 50}, 0), ({3: 2, 7: 30}, 0)]),
    "advanced_lightning": ("ADBE Lightning 2", [({}, 0), ({4: 25, 6: 3, 11: 50, 16: 2.5}, 0)]),
    "brush_strokes": ("ADBE Brush Strokes", [({2: 4, 3: 20}, 0), ({1: 45, 2: 5, 3: 40, 4: 2, 6: 3}, 0)]),
    "noise_hls_auto": ("ADBE Noise HLS Auto", [({2: 0.2, 3: 0.2, 4: 0.2}, 0), ({1: 3, 3: 0.4, 5: 4}, 0)]),
    # lot 13 (unverified until rendered)
    "cc_composite": ("CS Composite", [({2: 50}, 0), ({3: 6}, 0), ({3: 1, 4: 0}, 0)]),
    "paint_bucket": ("ADBE Paint Bucket", [({1: [600, 340], 3: 10}, 0), ({1: [380, 330], 2: 2, 3: 40, 8: [0, 0.4, 1, 1]}, 0), ({1: [5, 5], 2: 3, 4: 1}, 0)]),
    "cc_mr_smoothie": ("CC Mr. Smoothie", [({}, 0), ({4: [60, 330], 5: [600, 40], 7: 3, 6: 90}, 0)]),
    "cc_warpomatic": ("CC WarpoMatic", [({1: 40}, 0), ({1: 60, 5: 60, 6: 3, 7: 40}, 0)]),
    "cc_glue_gun": ("CC Glue Gun", [({}, 0), ({1: [200, 120], 2: 120, 6: 100}, 0)]),
    "key_cleaner": ("ADBE KeyCleaner", [({1: 6, 3: 100}, 0), ({1: 2, 3: 50, 4: 60}, 0)]),
}
NEEDS_MAP = {"displacement_map": {1: 2},   # param 1 (map layer) = layer index 2 (the map footage)
             "blend": {1: 2},
             "calculations": {6: 2},
             "compound_arithmetic": {1: 2},
             "difference_matte": {2: 2},
             "texturize": {1: 2},
             "compound_blur": {1: 2},
             "cc_image_wipe": {5: 2},
             "three_d_glasses": {2: 2},
             "cc_glass_wipe": {2: 2},
             "cc_warpomatic": {2: 2},
             }


def renders():
    out = []
    for slug, (mn, sets) in SPEC.items():
        for k, (vals, held) in enumerate(sets):
            out.append(dict(slug=slug, mn=mn, k=k, vals={str(i): v for i, v in vals.items()}, held=bool(held),
                            png=os.path.join("ae_holdout" if held else "ae", f"{slug}_{k}.png")))     # relative to D
    return out


if __name__ == "__main__":
    rs = renders()
    json.dump(rs, open(os.path.join(D, "renders.json"), "w", encoding="utf-8"), indent=1)
    print(len(rs), "renders,", sum(r["held"] for r in rs), "held out,", len(SPEC), "effects")
