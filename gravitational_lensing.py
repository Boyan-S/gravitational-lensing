"""
Gravitational Lensing Simulation
==================================
Simulates a black hole passing diagonally (top-left → bottom-right) in front
of a background galaxy image (e.g. Hubble Deep Field).

Physics:
--------
Weak-field (far-source) GR deflection for a point mass M:

    alpha_vec(theta) = theta_E^2 * (theta - theta_L) / |theta - theta_L|^2

Inverse raytracing (backward mapping) per pixel:

    beta = theta - alpha(theta)

i.e. each output pixel samples the background at its unlensed source position.

The Einstein ring appears where |theta - theta_L| = theta_E, i.e. the ring of
radius theta_E centred on the lens position.

Usage:
------
    python gravitational_lensing.py --image hubble.jpg
    python gravitational_lensing.py --image hubble.jpg --output lensing.mp4 \\
        --frames 120 --fps 24 --einstein_radius 80 --shadow_radius 55
"""

import argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from scipy.ndimage import map_coordinates
from PIL import Image


# ---------------------------------------------------------------------------
# Lensing core
# ---------------------------------------------------------------------------

def schwarzschild_px(einstein_r_px, D_l_px):
    """
    Derive the Schwarzschild radius in pixels from the Einstein radius and
    the lens distance, both in the same pixel units.

    From the Einstein radius definition:
        theta_E^2 = (4GM/c^2) * D_ls / (D_l * D_s)

    Solving for GM/c^2 and projecting onto the image plane:
        theta_s = 2GM / (c^2 * D_l) = theta_E^2 / (2 * D_l)   [far-source limit]

    Parameters
    ----------
    einstein_r_px : Einstein radius in pixels
    D_l_px        : Distance to lens in pixels (same angular scale)

    Returns
    -------
    Schwarzschild radius in pixels (used as the singularity guard)
    """
    return (einstein_r_px ** 2) / (2.0 * D_l_px)


def lens_frame(bg_rgb, lx, ly, einstein_r, shadow_r):
    """
    Produce one lensed RGB frame (H x W x 3, float32 0-1).

    Both the primary image (outside Einstein ring) and the secondary image
    (inside Einstein ring, de-magnified, flipped parity) are rendered — they
    emerge naturally from the inverse raytracing math without any masking.

    The only thing blacked out is a tiny region right at the singularity
    (r < shadow_r, typically just 2-3 pixels) where the deflection diverges
    numerically. This is not a physical shadow disk — just a numerical guard.

    Parameters
    ----------
    bg_rgb       : (H, W, 3) float32 background image, values in [0, 1]
    lx, ly       : lens centre in pixel coords (col, row)
    einstein_r   : Einstein radius in pixels
    shadow_r     : singularity guard radius in pixels (keep small, e.g. 3-5px)
    """
    H, W = bg_rgb.shape[:2]

    # Pixel coordinate grids
    cols = np.arange(W, dtype=np.float64)
    rows = np.arange(H, dtype=np.float64)
    C, R = np.meshgrid(cols, rows)           # (H, W) each

    # Vector from lens to each pixel
    dx = C - lx
    dy = R - ly
    r2 = dx**2 + dy**2
    r2_safe = np.where(r2 < 1e-6, 1e-6, r2)

    # Deflection field:  alpha = theta_E^2 * (r_vec / r^2)
    scale = (einstein_r ** 2) / r2_safe
    alpha_x = scale * dx
    alpha_y = scale * dy

    # Inverse raytrace: source-plane position for each output pixel.
    # Pixels OUTSIDE the Einstein ring → primary image   (same side as source)
    # Pixels INSIDE  the Einstein ring → secondary image (opposite side, de-mag)
    # Both are computed by exactly the same formula — no special casing needed.
    src_col = np.clip(C - alpha_x, 0, W - 1)
    src_row = np.clip(R - alpha_y, 0, H - 1)

    coords = np.array([src_row.ravel(), src_col.ravel()])

    # Bilinear sampling of each colour channel
    out = np.zeros((H, W, 3), dtype=np.float32)
    for ch in range(3):
        channel = bg_rgb[:, :, ch].astype(np.float64)
        sampled = map_coordinates(channel, coords, order=1, mode='nearest')
        out[:, :, ch] = sampled.reshape(H, W).astype(np.float32)

    r = np.sqrt(r2)

    # --- Singularity guard: tiny black dot at r < shadow_r only ---
    # This is purely numerical, not a physical shadow disk.
    # Keep shadow_r small (a few pixels); it does NOT swallow the secondary image.
    feather = 2.0
    singularity_mask = np.clip((r - shadow_r) / feather, 0, 1)  # 0 at centre, 1 outside
    out *= singularity_mask[:, :, np.newaxis]

    return out


# ---------------------------------------------------------------------------
# Animation driver
# ---------------------------------------------------------------------------

def run(args):
    # Load & normalise background image
    img = Image.open(args.image).convert("RGB")
    if args.scale != 1.0:
        W, H = img.size
        img = img.resize((int(W * args.scale), int(H * args.scale)), Image.LANCZOS)
    bg = np.array(img, dtype=np.float32) / 255.0
    H, W = bg.shape[:2]

    # Derive the singularity guard radius from the Einstein radius and D_l.
    # D_l is expressed in pixels (same angular units as einstein_radius).
    # theta_s = theta_E^2 / (2 * D_l)   [far-source limit]
    guard_r = schwarzschild_px(args.einstein_radius, args.D_l)

    print(f"Background: {W}x{H}  |  frames: {args.frames}  |  fps: {args.fps}")
    print(f"Einstein radius: {args.einstein_radius}px  |  D_l: {args.D_l}px")
    print(f"Schwarzschild guard radius: {guard_r:.4f}px")

    # Diagonal trajectory: top-left corner → bottom-right corner
    # Start and end well outside the frame
    margin = max(args.einstein_radius * 2, 80)
    x_start, y_start = -margin,      -margin
    x_end,   y_end   =  W + margin,   H + margin

    fig, ax = plt.subplots(figsize=(W / 100, H / 100), dpi=100)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    ax.axis("off")

    # First frame: lens far off-screen → essentially unlensed
    im_display = ax.imshow(bg, origin="upper", interpolation="nearest",
                           vmin=0, vmax=1)

    pbar_every = max(1, args.frames // 20)

    def update(frame_idx):
        t = frame_idx / max(args.frames - 1, 1)        # 0 → 1
        lx = x_start + t * (x_end - x_start)
        ly = y_start + t * (y_end - y_start)

        lensed = lens_frame(bg, lx, ly, args.einstein_radius, guard_r)
        im_display.set_data(lensed)

        if frame_idx % pbar_every == 0:
            print(f"  frame {frame_idx+1}/{args.frames}  lens=({lx:.1f}, {ly:.1f})")
        return (im_display,)

    ani = animation.FuncAnimation(
        fig, update, frames=args.frames, blit=True, interval=1000 / args.fps
    )

    writer = animation.FFMpegWriter(fps=args.fps, bitrate=4000,
                                    extra_args=["-pix_fmt", "yuv420p"])
    print(f"Saving -> {args.output}")
    ani.save(args.output, writer=writer)
    plt.close(fig)
    print("Done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gravitational lensing animation")
    parser.add_argument("--image",           required=True,
                        help="Path to background galaxy/nebula image (e.g. hubble.jpg)")
    parser.add_argument("--output",          default="lensing.mp4",
                        help="Output video filename (default: lensing.mp4)")
    parser.add_argument("--frames",          type=int,   default=120,
                        help="Total number of frames (default: 120)")
    parser.add_argument("--fps",             type=int,   default=24,
                        help="Frames per second (default: 24)")
    parser.add_argument("--einstein_radius", type=float, default=80,
                        help="Einstein ring radius in pixels (default: 80)")
    parser.add_argument("--D_l",             type=float, default=1e6,
                        help="Distance to lens in pixels (same angular units as "
                             "einstein_radius). Determines the Schwarzschild guard "
                             "radius: r_s = theta_E^2 / (2 * D_l). "
                             "Larger D_l = more distant/lighter black hole = smaller guard. "
                             "(default: 1e6, giving a sub-pixel guard for typical theta_E)")
    parser.add_argument("--scale",           type=float, default=1.0,
                        help="Resize background by this factor for speed (default: 1.0)")
    args = parser.parse_args()
    run(args)
