```yaml
Skill_Name: Skill_Architect_Specification
Purpose: Instructs the Agent on how to design, structure, and write highly optimized, standardized Skills for other Agents.
Execution_Trigger: Activated when the user requests the creation of a new Skill, Agent instruction set, or system prompt template.
```

## Section Index & Dependencies
- Upstream Dependencies:
  - User Goal Alignment (Grill-me Protocol)
  - Target Agent Capability Profile
- Downstream Dependencies:
  - Agent System Prompt Compiler
  - Context Caching Evaluation Pipeline

### Overview of Skill Construction
This document establishes the official technical blueprint for creating standardized Agent Skills. It defines the structural layout, markdown styling limits, the interactive "Grill-me" design protocol, and the dual-nature template pattern. Reading Agents may selectively load specific chapters of this document as needed during the prompt compilation phase.

### Detail 1: Structure and Token Limits
Every generated Skill must follow a strict, predictable four-part structural layout to maximize parser reliability and token economy.
- The structure consists of:
  - A YAML Header containing metadata (Name, Purpose, Trigger).
  - An Index Section mapping upstream and downstream system dependencies.
  - An Overview Section acting as a modular table of contents.
  - Detailed Expansion Sections that map directly to the Overview headings.
- Token allocation must be strictly managed to leverage context caching:
  - Each individual section must remain under 500 tokens.
  - The total length of the generated Skill must fall between 1500 and 6000 tokens.

### Detail 2: Markdown and Formatting Constraints
To prevent parser confusion and minimize cognitive load on the executing Agent, visual formatting must remain simple and flat.
- Header levels are restricted to exactly three types within the entire document:
  - H2 (##) for major structural divisions (e.g., Index).
  - H3 (###) for the Overview and all Detail headings.
  - H4 (####) for minor rules or sub-points within details.
  - H1 (#) is strictly prohibited.
- List nesting must not exceed three levels of indentation:
  - Level 1: Primary bullet points.
    - Level 2: Secondary nested details.
      - Level 3: Tertiary examples or specific exceptions.
  - Further nesting or deeper indentation is not allowed.

### Detail 3: Grill-Me Clarification Protocol
When the target domain, task boundaries, or user requirements are not fully transparent, the Agent must prioritize alignment over generation.
- The Agent must execute the "Grill-me" interactive protocol:
  - Halt generation and ask up to three highly targeted questions.
  - Present logical assumptions or hypotheses for user confirmation.
  - Refrain from writing the final Skill until all core operational parameters are resolved.
- This interactive loop prevents the generation of vague, generic, or non-functional instructions.

### Detail 4: Dual-Nature and Embedded Templates
Skills are dual-natured tools; they must guide both the generation of outputs (writing) and the evaluation of inputs (reading).
- To reduce the runtime cognitive load of the executing Agent:
  - Every Skill should embed rigid Markdown templates for data input and output.
  - The Skill must instruct the Agent to enforce these templates strictly.
- By structuring inputs and outputs into predictable schemas, the Agent bypasses structural decision-making and focuses processing power entirely on content execution.