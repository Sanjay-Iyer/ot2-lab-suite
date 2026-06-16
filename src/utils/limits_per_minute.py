import time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Any

@dataclass
class RateLimitGuard:
    """
    A conservative rate-limiting utility for local testing of LLM agents.
    Tracks calls in a rolling 60-second window and enforces delays/pauses.
    """
    max_calls_per_minute: int = 15
    safe_calls_per_minute: int = 10
    natural_delay_seconds: int = 5
    pause_seconds: int = 55
    enabled: bool = True
    
    # Store timestamps of calls in a double-ended queue
    call_timestamps: deque = field(default_factory=deque)

    def _clean_window(self):
        """Remove timestamps older than 60 seconds."""
        now = time.time()
        while self.call_timestamps and (now - self.call_timestamps[0] > 60):
            self.call_timestamps.popleft()

    def before_call(self):
        """Check limits and apply delays before an LLM call."""
        if not self.enabled:
            return

        self._clean_window()
        num_calls = len(self.call_timestamps)

        # Enforce Safe Limit Pause
        if num_calls >= self.safe_calls_per_minute:
            print(f"\n[RATE LIMIT GUARD] Safe limit reached ({num_calls} calls in 60s).")
            print(f"[RATE LIMIT GUARD] Pausing for {self.pause_seconds} seconds to avoid 429...")
            time.sleep(self.pause_seconds)
            self._clean_window()
        
        # Enforce Natural Delay between calls
        elif num_calls > 0:
            print(f"[RATE LIMIT GUARD] {num_calls} calls in window. Waiting {self.natural_delay_seconds}s natural delay...")
            time.sleep(self.natural_delay_seconds)

    def after_call(self):
        """Record the timestamp after a successful call."""
        if not self.enabled:
            return
        self.call_timestamps.append(time.time())
        num_calls = len(self.call_timestamps)
        if self.enabled:
            print(f"[RATE LIMIT GUARD] Call recorded. Total calls in window: {num_calls}")

    def invoke_with_limit(self, llm: Any, messages: List[Any]) -> Any:
        """Wrapper to invoke an LLM with rate limiting applied."""
        self.before_call()
        try:
            response = llm.invoke(messages)
            self.after_call()
            return response
        except Exception as e:
            print(f"[LLM CALL] Failed after rate-limit guard: {e}")
            raise e

if __name__ == "__main__":
    # Test Block: Simulate 12 calls to see the guard in action
    print("--- Testing RateLimitGuard (Safe Limit: 10, Pause: 5s for demo) ---")
    guard = RateLimitGuard(safe_calls_per_minute=10, pause_seconds=5, enabled=True)
    
    # Mock LLM object
    class MockLLM:
        def invoke(self, msg):
            print(f"  > Executing LLM Call...")
            return "Response"

    mock_llm = MockLLM()
    
    for i in range(1, 13):
        print(f"\nAttempting Call #{i}")
        guard.invoke_with_limit(mock_llm, [f"Message {i}"])
    
    print("\nTest Complete.")
