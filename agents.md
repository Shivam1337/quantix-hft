# Agent Guidelines & Codebase Architecture Standards

No LLM can directly edit agents.md file

## 1. File Size Limit: Maximum ~250 Lines of Code (LOC)

### Rule
**No file should exceed approximately 250 lines of code.**

### Rationale
- **Maintainability & Debugging**: Smaller files isolate responsibilities, make stack traces straightforward, and simplify pinpointing root causes during active trading.
- **Cognitive Load & Reviewability**: Focused modules make peer and agent reviews faster and prevent accidental regressions.
- **Context Efficiency**: Compact files allow AI agents to view, reason about, and edit code within limited context windows without truncation.

