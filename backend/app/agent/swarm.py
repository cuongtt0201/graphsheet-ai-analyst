"""Swarm Context Hub - The Latent Memory Space for Cross-Agent Knowledge Transfer.

In 2026 architectures, agents do not operate in isolation. DataAgent (which cleans
and joins data) and CodeAgent (which builds dashboards) share a "Swarm Memory Space".
This module builds a unified string block containing:
1. Long-term Behaviors (Habits & Preferences distilled from Neo4j).
2. Muscle Memory (Reusable Python Skills).
3. Ephemeral Broadcasts (Live insights passed between agents mid-session).

This block is injected into EVERY agent's prompt, ensuring they all possess the
same foundational knowledge and can instinctively cooperate.
"""

from app.agent.skills_manager import get_relevant_skills
from app.memory import graph


def build_swarm_context(user_id: str, query: str = "", local_broadcasts: list[str] | None = None) -> str:
    """Builds the universal Latent Swarm Memory block for prompt injection."""
    if not user_id:
        return ""
        
    parts = []
    
    # 1. Ephemeral Broadcasts (Swarm Telepathy)
    # E.g., DataAgent warns CodeAgent about duplicate rows or non-additive columns.
    if local_broadcasts:
        parts.append("LIVE SWARM BROADCASTS (Pay immediate attention to these facts):")
        for b in local_broadcasts:
            parts.append(f"- 🚨 {b}")
        parts.append("")
        
    # 2. Long-term Behaviors (Semantic Memory)
    behaviors = graph.get_behaviors(user_id)
    if behaviors:
        parts.append("USER'S LONG-TERM MEMORY (Habits & Preferences):")
        for b in behaviors:
            parts.append(f"- {b['description']}")
        parts.append("")
        
    # 3. Muscle Memory (Skills)
    # We fetch top 5 skills relevant to the current query so we don't blow up the context window.
    if query:
        skills = get_relevant_skills(query, owner_id=user_id, top_k=5)
    else:
        # If no query, fetch top generic skills (e.g., during datagen)
        skills = graph.get_personal_skills(user_id)[:5]
        
    if skills:
        parts.append("SWARM MUSCLE MEMORY (Reusable Python functions loaded in execution memory - CALL THEM DIRECTLY):")
        for s in skills:
            parts.append(f"- `{s['name']}`: {s.get('description', '')}")
        parts.append("")
        
    if not parts:
        return ""
        
    return "\n=== SWARM MEMORY SPACE ===\n" + "\n".join(parts) + "==========================\n"
