"""
Quick test for Layer 4 — Timing Check (UTC hour ∈ {13, 15, 20})
Run: python test_layer4.py
"""

import datetime
from src.indicators import is_good_hour, GOOD_HOURS_UTC


def test_layer4():
    print("=" * 52)
    print("  LAYER 4 TEST — Entry Timing Check")
    print("=" * 52)

    now_utc = datetime.datetime.utcnow()
    print(f"\n🕐 Current UTC time: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱  Current UTC hour: {now_utc.hour}:00")
    print(f"✅ Good hours:       {sorted(GOOD_HOURS_UTC)} UTC")

    signal, current_hour = is_good_hour()

    print("\n🔍 Schedule:")
    for h in range(24):
        marker = "◀ NOW" if h == current_hour else ""
        status = "✅" if h in GOOD_HOURS_UTC else "  "
        print(f"   {status} {h:02d}:00 UTC {marker}")

    print("\n" + "=" * 52)
    if signal:
        print(f"  ✅ LAYER 4 PASSED — Good time to enter!")
        print(
            f"     Hour {current_hour}:00 UTC is in {sorted(GOOD_HOURS_UTC)}")
    else:
        next_good = min(
            (h for h in GOOD_HOURS_UTC if h > current_hour),
            default=min(GOOD_HOURS_UTC)
        )
        wait = (next_good - current_hour) % 24
        print(f"  ❌ LAYER 4 FAILED — Wrong time window")
        print(
            f"     Now: {current_hour}:00 UTC — next good hour in ~{wait}h ({next_good}:00 UTC)")
    print("=" * 52)


if __name__ == "__main__":
    test_layer4()
