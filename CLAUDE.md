```yaml
Name: CLAUDE.md (Agent Runtime & Skill Loader Guide)
Dependencies:
  - Skills/*.md (Individual Skill Specifications)
Function: Guides the Agent to implement progressive loading by caching only Skill headers and dynamically loading full content on demand.
```

---

## Startup Protocol — EXECUTE BEFORE FIRST RESPONSE

This protocol is mandatory. Execute these steps immediately at session start, before responding to any user message.

### Step 1: Scan Skills Directory
```
Glob Skills/*.md → obtain file list
```

### Step 2: Filter Internal Documents
- Exclude files starting with `_` (internal documents, not directly loadable)
- Examples: `_Doc_Architect_Skill.md`, `_Doc_System_Framework.md`
- These are loaded indirectly via routing Skills (e.g., `doc-write`)

### Step 3: Read YAML Headers Only
For each file found, `Read` enough lines to capture the entire opening YAML block (from `` ```yaml `` to the closing `` ``` ``). The YAML block length varies per skill — do NOT use a fixed line count. Stop reading at the closing `` ``` `` marker; do NOT read beyond it into the markdown body.

### Step 4: Build Skill Route Table
Extract these three fields from each YAML header (per Skill_Architect_Specification standard):
- `Skill_Name`
- `Purpose`
- `Execution_Trigger`

Register the extracted metadata as the **Skill Route Table** in session memory. Example format:

```
| Skill_Name | Purpose | Execution_Trigger | File |
|---|---|---|---|
| Skill_Architect_Specification | Instructs the Agent on how to design Skills | User requests new Skill creation | Skills/Skill_Architect_Specification.md |
```

### Step 5: Evaluate Triggers on Every User Request
For each user message, compare intent against `Execution_Trigger` values in the Route Table.
- **Match found** → `Read` the full content of that skill file. Apply its instructions for the current task.
- **No match** → Do not load any skill. Proceed normally.

---

## Skill Loader Specification

### Progressive Loading Strategy
A memory-optimization method: cache metadata first (Route Table), delay full-file reads until triggered. This keeps the context window lean at startup regardless of how many skills exist.

### Skill Header Format
Per `Skill_Architect_Specification.md`, every skill file MUST open with a YAML block containing exactly:
- `Skill_Name` — unique identifier
- `Purpose` — one-line description of what the skill does
- `Execution_Trigger` — condition that activates this skill

Files in `Skills/` that do not conform to this header format should not be indexed. Report the mismatch to the user.

### Internal Document Convention
- Files prefixed with `_` are internal documents — they are not loaded during startup and do not appear in the Route Table
- They are loaded indirectly when a routing Skill (e.g., `doc-write`) directs the Agent to read them
- This convention prevents large internal specifications from consuming context at startup

### Dynamic Activation Rules
- A skill's full content is loaded only when its `Execution_Trigger` matches user intent.
- The loaded instructions apply as temporary context for the duration of the current task only.
- After the task completes, the skill content is not retained in active context (the Route Table entry persists).

---

## Repository Commands [Placeholder]
- Build Commands: *[Pending]*
- Testing Commands: *[Pending]*

## Code Style & Guidelines [Placeholder]
- Style Rules: *[Pending]*
