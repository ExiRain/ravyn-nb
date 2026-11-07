"""
A donation's note lands where her notes about that person are read.

    python tests/test_worker_donations.py

Notes are read by user_key (YouTube's "youtube:<channel id>") when the PC
sends one, else by login. Donations were written by display name, so a
YouTube donor's "has donated" fact sat under "Patron" while she looked under
"youtube:UCpatron" — and a Twitch viewer called patron got the credit.
"""

import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules["pika"] = types.ModuleType("pika")
llm = types.ModuleType("adapters.llm.llama_server_client")
llm.run_llm = lambda *a, **k: {"text": ""}
llm.run_llm_simple = lambda *a, **k: ""
sys.modules["adapters.llm.llama_server_client"] = llm

import persona.memory as memory_module                    # noqa: E402
import adapters.mq.rabbitmq as worker                     # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def main():
    print("\n--- donations are filed under the key her notes use ---")
    with tempfile.TemporaryDirectory() as tmp:
        memory_module.MEMORY_FILE = Path(tmp) / "memory.json"
        m = memory_module.MemoryManager()
        m.donors.clear()
        worker.memory = m

        worker._note_donation({"user": "Patron", "user_key": "youtube:UCpatron",
                               "donation": {"amount": 10.0, "currency": "$"}})
        check("a YouTube donor's note is read back under their channel key",
              "donated once" in m.notes_for_prompt("youtube:UCpatron"),
              m.notes_for_prompt("youtube:UCpatron"))
        check("a Twitch viewer with the same name gets no credit",
              m.notes_for_prompt("patron") == "")

        worker._note_donation({"user": "bob",
                               "donation": {"amount": 3.0, "currency": "$"}})
        check("Twitch (no user_key) still keys by login, as before",
              "donated once" in m.notes_for_prompt("bob"))

        worker._note_donation({"user": "", "donation": {"amount": 1}})
        worker._note_donation({"user": "x"})
        check("no name, or no donation: nothing is written",
              "x" not in m.donors and "" not in m.donors)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
