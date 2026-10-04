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
    "channel_mixer": ("ADBE Channel Mixer", [({1: 50, 2: 50, 6: 80, 12: 20}, 0), ({13: 1, 1: 30, 2: 59, 3: 11}, 0),
                                             ({5: -50, 9: 120, 4: 10}, 0)]),
    "set_channels": ("ADBE Set Channels", [({2: 2, 4: 3, 6: 1}, 0), ({2: 5, 4: 6, 6: 7, 8: 9}, 0), ({2: 8, 4: 10, 6: 4}, 0)]),
    "offset": ("ADBE Offset", [({1: [400, 250]}, 0), ({1: [100.5, 40.25], 2: 30}, 0)]),
    "radial_wipe": ("ADBE Radial Wipe", [({1: 30}, 0), ({1: 50, 2: 45, 4: 2, 5: 20}, 0), ({1: 40, 3: [200, 120], 4: 3}, 0)]),
    "venetian_blinds": ("ADBE Venetian Blinds", [({1: 40}, 0), ({1: 60, 2: 30, 3: 25, 4: 5}, 0), ({1: 50, 2: 90, 3: 16}, 0)]),
    "photo_filter": ("ADBE Photo Filter", [({}, 0), ({1: 12, 3: 60}, 0), ({1: 4, 3: 80, 4: 0}, 0)]),
    "vibrance": ("ADBE Vibrance", [({1: 60}, 0), ({1: -50}, 0), ({1: 30, 2: 40}, 0), ({2: -80}, 0)]),
    # lot 2 (unverified until rendered): visible settings only; held-out ones are to be added independently
    "threshold": ("ADBE Threshold2", [({1: 128}, 0), ({1: 60}, 0), ({1: 200}, 0)]),
    "gamma_pedestal_gain": ("ADBE Gamma/Pedestal/Gain", [({2: 0.6, 5: 1.4, 8: 1.0}, 0), ({3: 0.1, 4: 0.9, 10: 0.7}, 0), ({1: 2.0}, 0)]),
    "leave_color": ("ADBE Leave Color", [({1: 100, 2: [1, 0, 0, 1], 3: 30, 4: 10}, 0), ({1: 100, 2: [0, 0.3, 1, 1], 3: 15, 4: 20, 5: 2}, 0), ({1: 60, 2: [1, 1, 0, 1], 3: 40}, 0)]),
    "sharpen": ("ADBE Sharpen", [({1: 20}, 0), ({1: 60}, 0)]),
    "find_edges": ("ADBE Find Edges", [({}, 0), ({1: 1}, 0), ({2: 50}, 0)]),
    "unsharp_mask": ("ADBE Unsharp Mask2", [({1: 100, 2: 3}, 0), ({1: 200, 2: 8, 3: 10}, 0)]),
    "mirror": ("ADBE Mirror", [({1: [320, 180]}, 0), ({1: [200, 150], 2: 30}, 0), ({1: [320, 100], 2: 90}, 0)]),
    "polar_coordinates": ("ADBE Polar Coordinates", [({1: 100}, 0), ({1: 100, 2: 2}, 0), ({1: 50}, 0)]),
    "twirl": ("ADBE Twirl", [({1: 90}, 0), ({1: -200, 2: 60}, 0), ({1: 45, 3: [200, 120], 2: 40}, 0)]),
    "bulge": ("ADBE Bulge", [({1: 120, 2: 120}, 0), ({1: 150, 2: 80, 4: -0.8}, 0), ({1: 100, 2: 100, 3: [200, 120], 4: 2}, 0)]),
    "spherize": ("ADBE Spherize", [({1: 150}, 0), ({1: 100, 2: [220, 140]}, 0)]),
    "radial_blur": ("ADBE Radial Blur", [({1: 20}, 0), ({1: 30, 3: 2}, 0), ({1: 15, 2: [200, 120]}, 0)]),
    "cc_vignette": ("CC Vignette", [({1: 80}, 0), ({1: 100, 2: 120}, 0), ({1: 60, 3: [200, 120]}, 0)]),
    "iris_wipe": ("ADBE Iris Wipe", [({3: 120}, 0), ({2: 10, 3: 150, 4: 1, 5: 60, 6: 20}, 0), ({1: [200, 140], 2: 8, 3: 100, 7: 15}, 0)]),
    # lot 3 (unverified until rendered)
    "color_balance": ("ADBE Color Balance 2", [({1: 40, 6: -30, 9: 50}, 0), ({4: 60, 5: -20, 10: 1}, 0)]),
    "change_color": ("ADBE Change Color", [({2: 120, 5: [1, 0, 0, 1], 6: 30, 7: 10}, 0), ({1: 2, 5: [0, 0.4, 1, 1], 6: 20, 7: 20, 8: 2}, 0), ({2: -60, 3: 20, 5: [1, 1, 0, 1], 6: 40}, 0)]),
    "cc_toner": ("CC Toner", [({}, 0), ({1: 1, 2: [1, 0.9, 0.6, 1], 6: [0.1, 0, 0.3, 1]}, 0), ({1: 3, 7: 30}, 0)]),
    "cc_color_offset": ("CC Color Offset", [({1: 90, 2: 180, 3: 270}, 0), ({1: 120, 4: 2}, 0), ({2: 200, 4: 3}, 0)]),
    "arithmetic": ("ADBE Arithmetic", [({1: 3, 2: 128, 3: 64, 4: 200}, 0), ({1: 4, 2: 100, 3: 100, 4: 100, 5: 0}, 0), ({1: 11, 2: 128, 3: 128, 4: 128}, 0)]),
    "remove_color_matting": ("ADBE Remove Color Matting", [({1: [1, 1, 1, 1]}, 0), ({1: [0.5, 0.5, 0.5, 1]}, 0)]),
    "color_key": ("ADBE Color Key", [({1: [1, 0.2, 0.2, 1], 2: 60}, 0), ({1: [0, 1, 0, 1], 2: 120, 4: 3}, 0)]),
    "extract": ("ADBE Extract", [({3: 60, 4: 200}, 0), ({2: 2, 3: 100, 5: 40}, 0), ({3: 80, 4: 180, 7: 1}, 0)]),
    "spill_suppressor": ("ADBE Spill Suppressor", [({}, 0), ({1: [0, 0, 1, 1], 3: 60}, 0)]),
    "cc_threshold": ("CC Threshold", [({}, 0), ({1: 80, 2: 2, 3: 1}, 0), ({1: 160, 4: 40}, 0)]),
    "cc_threshold_rgb": ("CC Threshold RGB", [({}, 0), ({1: 60, 2: 200, 3: 100, 5: 1}, 0)]),
    "median": ("ADBE Median", [({1: 3}, 0), ({1: 8, 2: 1}, 0)]),
    # lot 4 (unverified until rendered)
    "wave_warp": ("ADBE Wave Warp", [({}, 0), ({1: 3, 2: 20, 3: 80, 4: 30}, 0), ({1: 2, 2: 6, 3: 25, 7: 90}, 0)]),
    "ripple": ("ADBE Ripple", [({}, 0), ({1: 80, 5: 40, 6: 10, 7: 90}, 0), ({2: [200, 120], 5: 12, 6: 6}, 0)]),
    "cc_tiler": ("CC Tiler", [({}, 0), ({1: 50, 2: [200, 120]}, 0), ({1: 33, 3: 40}, 0)]),
    "grid": ("ADBE Grid", [({}, 0), ({2: 3, 4: 60, 5: 30, 6: 4, 9: [1, 0, 0, 1], 11: 2}, 0)]),
    "ellipse": ("ADBE Ellipse", [({}, 0), ({2: 400, 3: 200, 4: 40, 5: 50, 6: [1, 0.8, 0, 1], 7: [1, 0, 0.4, 1], 8: 1}, 0)]),
    "bevel_alpha": ("ADBE Bevel Alpha", [({}, 0), ({1: 6, 2: 45, 4: 0.8}, 0)]),
    "radial_shadow": ("ADBE Radial Shadow", [({3: [100, 50], 4: 20}, 0), ({1: [0.4, 0, 0.6, 1], 2: 80, 3: [500, 300], 4: 40, 8: 1}, 0)]),
    "channel_blur": ("ADBE Channel Blur", [({1: 20}, 0), ({1: 5, 2: 15, 3: 30, 6: 2}, 0), ({4: 12, 5: 1}, 0)]),
    "cc_cross_blur": ("CC Cross Blur", [({1: 20, 2: 20}, 0), ({1: 40, 2: 5, 4: 1}, 0)]),
    "bilateral_blur": ("ADBE Bilateral Blur", [({}, 0), ({1: 12, 2: 40}, 0)]),
    # lot 5 (unverified until rendered; random effects: amount and scale only, AE's generator is not public)
    "gradient_wipe": ("ADBE Gradient Wipe", [({1: 40}, 0), ({1: 50, 2: 30}, 0), ({1: 30, 5: 1}, 0)]),
    "block_dissolve": ("ADBE Block Dissolve", [({1: 40, 2: 20, 3: 20}, 0), ({1: 70, 2: 8, 3: 40}, 0)]),
    "circle": ("ADBE Circle", [({}, 0), ({1: [200, 120], 2: 100, 9: [1, 0.5, 0, 1], 11: 2}, 0)]),
    "cc_light_rays": ("CC Light Rays", [({}, 0), ({1: 200, 2: [500, 100], 3: 70}, 0)]),
    "cc_light_burst": ("CC Light Burst 2.5", [({}, 0), ({1: [200, 120], 3: 80, 4: 1}, 0)]),
    "cc_spotlight": ("CC Spotlight", [({}, 0), ({1: [100, 50], 2: [400, 220], 4: 40, 5: 50, 6: [1, 0.8, 0.4, 1], 8: 2}, 0)]),
    "noise": ("ADBE Noise", [({1: 30}, 0), ({1: 60, 2: 0}, 0)]),
    "scatter": ("ADBE Scatter", [({1: 5}, 0), ({1: 12, 2: 2}, 0)]),
    "broadcast_colors": ("ADBE Broadcast Colors", [({3: 90}, 0), ({2: 2, 3: 85}, 0), ({1: 2, 2: 3, 3: 95}, 0)]),
    # lot 6 (unverified until rendered)
    "cc_power_pin": ("CC Power Pin", [({1: [60, 20], 2: [600, 40], 3: [20, 340], 4: [620, 300]}, 0), ({1: [0, 60], 2: [640, 0], 3: [100, 360], 4: [560, 330]}, 0)]),
    "cc_radial_scalewipe": ("CC Radial ScaleWipe", [({1: 30}, 0), ({1: 50, 2: [200, 120]}, 0), ({1: 40, 3: 1}, 0)]),
    "cc_slant": ("CC Slant", [({1: 30}, 0), ({1: -20, 3: 60, 4: 300, 5: 1, 6: [0.2, 0.2, 0.2, 1]}, 0)]),
    "cc_split": ("CC Split", [({3: 60}, 0), ({1: [100, 60], 2: [500, 300], 3: 100}, 0)]),
    "cc_lens": ("CC Lens", [({}, 0), ({2: 80, 3: 50}, 0), ({1: [200, 120], 3: -40}, 0)]),
    "cc_kaleida": ("CC Kaleida", [({2: 50}, 0), ({1: [200, 120], 2: 30, 4: 25}, 0)]),
    "solid_composite": ("ADBE Solid Composite", [({2: [0.2, 0.4, 0.8, 1]}, 0), ({1: 50, 2: [1, 0.8, 0, 1], 3: 70}, 0)]),
    "dust_scratches": ("ADBE Dust & Scratches", [({1: 3, 2: 20}, 0), ({1: 6, 2: 5}, 0)]),
    "noise_alpha": ("ADBE Noise Alpha", [({2: 50}, 0), ({1: 2, 2: 80, 3: 3}, 0)]),
    "noise_hls": ("ADBE Noise HLS2", [({2: 20, 3: 20, 4: 20}, 0), ({1: 3, 3: 40, 5: 4}, 0)]),
    "color_emboss": ("ADBE Color Emboss", [({}, 0), ({1: 120, 2: 3, 3: 200}, 0)]),
}
NEEDS_MAP = {"displacement_map": {1: 2}}   # param 1 (map layer) = layer index 2 (the map footage)


def renders():
    out = []
    for slug, (mn, sets) in SPEC.items():
        for k, (vals, held) in enumerate(sets):
            out.append(dict(slug=slug, mn=mn, k=k, vals={str(i): v for i, v in vals.items()}, held=bool(held),
                            png=os.path.join("ae_holdout" if held else "ae", f"{slug}_{k}.png")))     # relative to D
    return out


if __name__ == "__main__":
    rs = renders()
    json.dump(rs, open(os.path.join(D, "renders.json"), "w"), indent=1)
    print(len(rs), "renders,", sum(r["held"] for r in rs), "held out,", len(SPEC), "effects")
