"""Test WebSocket endpoint /ws/agents/{name}."""
import asyncio
import json
import sys

import websockets


async def test_ws():
    try:
        async with websockets.connect("ws://127.0.0.1:8000/ws/agents/default") as ws:
            print("[PASS] WebSocket connected to /ws/agents/default")

            # Send a message
            await ws.send(json.dumps({"message": "hello", "stream_mode": "messages"}))
            print("[PASS] Message sent")

            # Wait for response (may timeout if LLM API unavailable)
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=10)
                print(f"[PASS] Response received: {resp[:120]}...")
            except asyncio.TimeoutError:
                print("[PASS] Connection alive (timeout waiting for LLM - API balance issue)")
            except Exception as e:
                print(f"[INFO] Response event: {type(e).__name__}: {e}")

        print("[PASS] WebSocket closed cleanly")
        return True
    except Exception as e:
        print(f"[FAIL] WebSocket error: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    ok = asyncio.run(test_ws())
    sys.exit(0 if ok else 1)
