---
name: holepuncher
description: Hole punching analysis to find problems, false assumptions, vulnerabilities, missing knowledge and failure modes. Use before launches, for security and logic review, or when a plan feels too perfect.
user-invocable: true
---

# Hole Punching Analysis

Conduct an critical harsh analysis to find problems, false assumptions, vulnerabilities, missing knowledge and failure modes in a plan, system, or strategy.

## Instructions

Think like an attacker. Be the devil's advocate. Your mission is to find a gap and exploit it, find the areas where it could fail, and expose what could go wrong. Be wild, thorough and creative in attack.

### Output Format

**Target**: [What we're attacking]
**Objective**: [What would "breaking it" look like?]

---

## Target Understanding

**Summary of the Plan/System**
[Brief description of what we're analyzing]

**Key Assumptions**
- [Assumption 1]
- [Assumption 2]

---

## Attack Surface Analysis

**Entry Points / Vulnerabilities**
| Vector | Description | Severity |
|--------|-------------|----------|
| [attack vector] | [how it could be exploited] | Critical/High/Med/Low |

---

## Failure Mode Analysis

### Technical/Operational Failures
| Failure Mode | Trigger | Impact |
|--------------|---------|--------|
| [what could fail] | [what causes it] | [effect] |

### Human Failures
| Failure Mode | Trigger | Impact |
|--------------|---------|--------|
| [human error] | [situation] | [consequence] |

---

## Adversary Scenarios

**If I wanted this to fail, I would...**

### Scenario 1: [Attack Name]
- **Attack method**: [how they'd do it]
- **Likelihood of success**: [High/Med/Low]
- **Impact if successful**: [consequences]

### Scenario 2: [Attack Name]
- **Attack method**: [how]
- **Likelihood of success**: [High/Med/Low]
- **Impact if successful**: [consequences]

---

## Assumption Attacks

| Assumption | Attack | What If Wrong? |
|------------|--------|----------------|
| [assumption] | [challenge to it] | [consequences] |

---

## Blind Spot Analysis

**What are we not seeing?**
- [Blind spot 1]
- [Blind spot 2]

**What are we too optimistic about?**
- [Over-optimism 1]

---

## Hole Punching Findings

### Critical Vulnerabilities (Must Address)
| Vulnerability | Risk | Mitigation |
|---------------|------|-----------|
| [vulnerability] | [risk level] | [how to fix] |

### High-Priority Concerns
| Concern | Recommendation |
|---------|----------------|
| [concern] | [recommendation] |

---

## Hardening Recommendations

**Immediate actions**:
1. [Action 1]
2. [Action 2]

**Ongoing monitoring**:
1. [What to watch]

---

**Bottom Line**
> [Is this plan/system ready? What must change?]

## Guidelines

- Be adversarial, not just critical
- Remove assumptions, question everything
- Think creatively—real attackers don't follow rules
- Look for cascading failures
- Look for the small chinks in the armour which can be broken open
- The goal is to make it stronger, not just find flaws

$ARGUMENTS