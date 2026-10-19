# Agent Analytical Platform

An analytics agent that answers questions over the ecommerce and energy semantic layers, and remembers a user's own definitions across sessions.

## Agent memory

**Memory**:
One fact a user asked the agent to keep, such as a definition ("high-value means over $500") or a preference. It belongs to one user and is the only thing that can be forgotten.
_Avoid_: preference, fact, saved definition (as nouns for the stored thing)

**Entity mention**:
A fragment the extractor records alongside Memories (a name, a term) that surfaces in recall. It is context only and is never acted on.
_Avoid_: entity memory, ghost

**Rival**:
An existing Memory that defines the same term as a new one being saved. A term has at most one current Memory, so saving a redefinition means retiring its Rival.
_Avoid_: duplicate, conflict

**Memory store**:
Where a user's Memories live. It answers recall, keeps saves and forgets honest, and tells Memories apart from Entity mentions.
_Avoid_: mem0, Valkey (those are what it is built on, not what it is)
