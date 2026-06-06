```yaml
Name: CLAUDE.md (Agent Runtime & Skill Loader Guide)
Dependencies:
  - skills/*.md (Individual Skill Specifications)
Function: Guides the Agent to implement progressive loading by caching only Skill headers and dynamically loading full content on demand.
```

## Section Index & Dependencies
- Upstream Dependencies:
  - Repository Directory Structure (`skills/`)
- Downstream Dependencies:
  - Task Execution Runtime

### Overview of Agent Runtime Specification
This document establishes the execution protocol for the Agent within this repository, focusing strictly on progressive skill loading to optimize active context window usage. Reading Agents may selectively load specific sections based on their execution phase.

1. Skill Indexing Protocol: Scanning and registering only the metadata headers of available skills.
2. Progressive Loading & Activation: Dynamically reading the full content of a skill only when triggered.
3. Future Repository Specifications: Placeholder for commands, test suites, and project-specific guidelines.

#### Core Terminology Table
The following terminology is registered for this runtime environment:

| Term | Type | Definition |
| :--- | :--- | :--- |
| **Progressive Loading** | Strategy | A memory-optimization method that caches metadata first, delaying full-file reads until triggered. |
| **Skill Header** | Format | The YAML block at the beginning of each skill file declaring its name, purpose, and trigger. |
| **Dynamic Activation** | Action | The process of executing a full file read tool to load a skill's body once its trigger matches user intent. |

---

## Skill Loader Specifications

### Detail 1: Skill Indexing Protocol
Upon repository initialization, the Agent must not read the full contents of files inside the `skills/` directory.
- **Header Scanning Process:**
  - The Agent must scan the `skills/` directory.
  - Using file tools, the Agent must read only the initial YAML frontmatter (the Skill Header) of each markdown file.
- **Index Registration:**
  - Extract the `Name`, `Purpose`, and `Execution_Trigger` from each header.
  - Store this metadata in the active session memory as a "Skill Route Table" to serve as a lightweight lookup index.

### Detail 2: Progressive Loading & Activation
Full skill instructions must remain unloaded until explicitly required by the current task context.
- **Trigger Evaluation:**
  - For every user request, the Agent compares the input against the registered `Execution_Trigger` parameters in the Skill Route Table.
- **On-Demand Loading:**
  - Only when a trigger match is confirmed, the Agent executes a full read command (e.g., `view_file`) on that specific skill file.
  - The newly loaded instructions are then applied as temporary system prompts for the duration of the current task.
  - > Example: If the user requests a new skill generation, the Agent triggers and reads `skills/Doc_Architect_Skill.md` to guide the output.

---

## Future Repository Specifications

### Detail 3: Repository Commands [Placeholder]
- **Build Commands:**
  - *[Placeholder for future compilation and build commands]*
- **Testing Commands:**
  - *[Placeholder for future test execution commands]*

### Detail 4: Code Style & Guidelines [Placeholder]
- **Style Rules:**
  - *[Placeholder for future linter, formatting, and structural guidelines]*

---

## Appendix
> [1] `skills/` Directory: The dedicated storage for modular Agent skills adhering to the single responsibility principle.