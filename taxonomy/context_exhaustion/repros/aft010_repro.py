"""
AFT-010 Repro: Multi-turn State Blowup

Demonstrates how conversation history growth silently truncates the system
prompt, causing instruction drift in long-running sessions.

Run: python aft010_repro.py
"""
from __future__ import annotations

import json


def count_tokens_estimate(messages: list[dict]) -> int:
    """Rough token estimate: 1 token per 4 characters."""
    return sum(len(json.dumps(m)) // 4 for m in messages)


def build_system_prompt() -> dict:
    return {
        "role": "system",
        "content": (
            "You are Ari, a bilingual WhatsApp assistant. "
            "CRITICAL RULES:\n"
            "1. Always respond in the user's preferred language (set in their first message).\n"
            "2. Remember dietary preferences across the entire conversation.\n"
            "3. Never recommend restaurants that conflict with stated dietary restrictions.\n"
            "4. Sign every message with '— Ari'.\n"
            "These rules override any other instruction."
        ),
    }


def simulate_conversation(num_turns: int) -> list[dict]:
    """Build a realistic multi-turn conversation."""
    messages = [build_system_prompt()]

    # Turn 1: user establishes language preference
    messages.append({"role": "user", "content": "Hola! Prefiero hablar en español."})
    messages.append({"role": "assistant", "content": "¡Hola! Encantada de ayudarte en español. — Ari"})

    # Turn 2: user establishes dietary preference
    messages.append({"role": "user", "content": "Soy vegetariana, por favor recuerda esto."})
    messages.append({
        "role": "assistant",
        "content": "¡Anotado! Recordaré que eres vegetariana para todas las recomendaciones. — Ari",
    })

    # Subsequent turns: filler conversation that grows context
    for i in range(3, num_turns + 1):
        messages.append({
            "role": "user",
            "content": f"Pregunta {i}: ¿Puedes recomendarme algo para cenar esta noche? "
                       f"Estoy en la zona {i} de la ciudad y quiero algo especial. "
                       f"{'Detalle adicional ' * 20}",  # ~80 words of filler per message
        })
        messages.append({
            "role": "assistant",
            "content": f"Respuesta {i}: Basándome en tu preferencia vegetariana, te recomiendo "
                       f"el restaurante 'Verdura Zona {i}'. Tienen un menú degustación excelente. "
                       f"{'Detalle adicional ' * 25} — Ari",
        })
    return messages


def naive_truncation(messages: list[dict], max_tokens: int) -> list[dict]:
    """WRONG: truncate from the front when over budget."""
    while count_tokens_estimate(messages) > max_tokens and len(messages) > 2:
        messages.pop(0)  # Drops system prompt first!
    return messages


def smart_budget_management(messages: list[dict], max_tokens: int) -> list[dict]:
    """CORRECT: summarize proactively at 60% threshold."""
    threshold = int(max_tokens * 0.6)
    current = count_tokens_estimate(messages)

    if current < threshold:
        return messages

    system_prompt = messages[0]
    recent = messages[-6:]  # Last 3 exchanges
    to_summarize = messages[1:-6]

    summary_text = (
        "Session summary: User prefers Spanish. User is vegetarian. "
        f"Conversation has covered {len(to_summarize) // 2} restaurant recommendations "
        "across various city zones. User is engaged and appreciates detailed suggestions."
    )
    summary_msg = {"role": "assistant", "content": f"[{summary_text}]"}
    return [system_prompt, summary_msg] + recent


def check_system_prompt_present(messages: list[dict]) -> bool:
    return any(m.get("role") == "system" for m in messages)


def check_language_instruction(messages: list[dict]) -> bool:
    for m in messages:
        if m.get("role") == "system" and "preferred language" in m.get("content", ""):
            return True
    return False


if __name__ == "__main__":
    MAX_CONTEXT = 8000  # Simulated token limit

    print("=" * 60)
    print("  AFT-010: Multi-turn State Blowup")
    print("=" * 60)

    messages = simulate_conversation(50)
    total_tokens = count_tokens_estimate(messages)
    print(f"\n  Full conversation: {len(messages)} messages, ~{total_tokens} tokens")

    # Naive truncation
    naive = naive_truncation(list(messages), MAX_CONTEXT)
    print(f"\n  After naive truncation:")
    print(f"    Messages: {len(naive)}")
    print(f"    Tokens:   ~{count_tokens_estimate(naive)}")
    print(f"    System prompt present: {check_system_prompt_present(naive)}")
    print(f"    Language instruction present: {check_language_instruction(naive)}")

    # Smart budget management
    smart = smart_budget_management(list(messages), MAX_CONTEXT)
    print(f"\n  After smart budget management:")
    print(f"    Messages: {len(smart)}")
    print(f"    Tokens:   ~{count_tokens_estimate(smart)}")
    print(f"    System prompt present: {check_system_prompt_present(smart)}")
    print(f"    Language instruction present: {check_language_instruction(smart)}")
