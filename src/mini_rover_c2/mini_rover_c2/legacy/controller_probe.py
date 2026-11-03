"""Read-only gamepad diagnosis. No Tk, network, serial or motor commands."""
import argparse
import json
import time
from mini_rover_c2.legacy.xbox import Xbox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=15)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60:
        parser.error("Use 1 to 60 seconds")
    controller = Xbox()
    samples = 0
    peaks = [0., 0., 0., 0.]
    lb_seen = False
    error = None
    end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < end:
            try:
                _, _, held = controller.read()
                samples += 1
                lb_seen |= held
                peaks = [max(a, abs(b)) for a, b in zip(peaks, controller.axes)]
                error = None
            except RuntimeError as exc:
                error = str(exc)
            time.sleep(.02)
    finally:
        controller.close()
    print(json.dumps({"name": controller.name, "samples": samples,
                      "peak_absolute_LX_LY_RX_RY": peaks, "LB_seen": lb_seen,
                      "last_error": error}, indent=2))


if __name__ == "__main__":
    main()
