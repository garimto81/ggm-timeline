"""
E2E Test: Timeline Data Flow Verification
- WebApp에서 timeline 시트 데이터 수신
- Python 로직으로 Event 변환
- 결과 검증
"""

import sys
import json
from pathlib import Path

# 모듈 임포트
import ggm_io
import ggm_logic

def test_timeline_e2e():
    """E2E 테스트: WebApp → Python Event 변환 검증"""

    print("=" * 60)
    print("GGM Timeline E2E Test")
    print("=" * 60)

    # 1. 설정 로드
    print("\n[1] Loading config...")
    cfg = ggm_io.load_config()
    serialize_url = cfg.get("serialize_url", "")
    print(f"    serialize_url: {serialize_url[:50]}...")

    # 2. WebApp 호출 (직접 JSON 확인)
    print("\n[2] Fetching from WebApp (raw JSON)...")
    import urllib.request
    try:
        req = urllib.request.Request(serialize_url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_data = resp.read().decode("utf-8")
        raw_json = json.loads(raw_data)
    except Exception as e:
        print(f"    ERROR: {e}")
        return False

    ok = raw_json.get("ok", False)
    error = raw_json.get("error", "")
    row_list = raw_json.get("rows", [])
    hero_slot = raw_json.get("heroSlot", "")
    vill_slot = raw_json.get("villSlot", "")

    print(f"    ok: {ok}")
    if error:
        print(f"    error: {error}")
        return False
    print(f"    heroSlot: {hero_slot}")
    print(f"    villSlot: {vill_slot}")
    print(f"    rows count: {len(row_list)}")

    # 3. 첫 3개 행 샘플 출력 (raw JSON 필드명)
    print("\n[3] Sample rows (first 3):")
    for i, row in enumerate(row_list[:3]):
        sheet_name = row.get("SheetName", "?")
        hand = row.get("Hand", "?")
        cmd_type = row.get("CommandType", "?")
        seat_idx = row.get("SeatIndex", "?")
        action = row.get("Action", "?")
        print(f"    [{i+1}] SheetName={sheet_name}, CommandType={cmd_type}, "
              f"SeatIndex={seat_idx}, Action={action}")
        if hand:
            print(f"         Hand={hand[:25]}...")

    # 4. timeline 시트 확인
    print("\n[4] Verifying 'timeline' sheet...")
    timeline_rows = [r for r in row_list if r.get("SheetName") == "timeline"]
    sheet1_rows = [r for r in row_list if r.get("SheetName") == "Sheet1"]

    print(f"    timeline rows: {len(timeline_rows)}")
    print(f"    Sheet1 rows: {len(sheet1_rows)}")

    if len(timeline_rows) == 0 and len(sheet1_rows) > 0:
        print("    FAILED: Still using Sheet1, not timeline!")
        return False

    if len(timeline_rows) == 0:
        print("    FAILED: No rows from any sheet!")
        return False

    print("    OK: Using timeline sheet")

    # 5. Event 변환 테스트 (ggm_io.fetch_serialize_rows 사용)
    print("\n[5] Building timeline events...")
    try:
        rows = ggm_io.fetch_serialize_rows(cfg, quiet=True)
        events, deleted_keys = ggm_logic.build_timeline_from_rows(rows, 0)
        print(f"    Total events: {len(events)}")
        print(f"    Deleted keys: {deleted_keys}")
    except Exception as e:
        print(f"    ERROR during event build: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 6. 이벤트 종류별 카운트
    print("\n[6] Event breakdown:")
    kinds = {}
    for ev in events:
        kinds[ev.kind] = kinds.get(ev.kind, 0) + 1
    for kind, count in sorted(kinds.items()):
        print(f"    {kind}: {count}")

    # 7. 샘플 이벤트 출력
    print("\n[7] Sample events (first 5):")
    for i, ev in enumerate(events[:5]):
        print(f"    [{i+1}] time={ev.time_sec:.1f}s, kind={ev.kind}, "
              f"bcode={ev.bcode}, label={ev.label[:30] if ev.label else ''}")

    # 8. Hand 필드 확인 (신규 스키마 검증)
    print("\n[8] Verifying 'Hand' field (new schema):")
    hands_found = set()
    for row in row_list[:10]:
        hand = row.get("Hand", "")
        if hand:
            hands_found.add(hand[:20])

    if hands_found:
        print(f"    Hand values found: {len(hands_found)}")
        for h in list(hands_found)[:3]:
            print(f"      - {h}...")
        print("    OK: Hand field present (new schema)")
    else:
        print("    WARNING: No 'Hand' field found - may be using old schema!")

    # 9. Delete 플래그 확인
    print("\n[9] Checking Delete flag:")
    deleted_rows = [r for r in row_list if r.get("Delete") == True]
    print(f"    Rows with Delete=true: {len(deleted_rows)}")

    # 결과
    print("\n" + "=" * 60)
    success = len(timeline_rows) > 0 and len(events) > 0
    print(f"E2E TEST RESULT: {'PASSED' if success else 'FAILED'}")
    print("=" * 60)

    return success


if __name__ == "__main__":
    success = test_timeline_e2e()
    sys.exit(0 if success else 1)
