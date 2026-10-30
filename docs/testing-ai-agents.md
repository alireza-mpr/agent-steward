# Evals: Testing an AI Agent When Answers Are Unpredictable

When you write standard code, testing is straightforward: you give it a specific input, and it gives you a specific output. If it works once, it will keep working until you change the code. 

Testing an AI model is different. If you ask an AI model the exact same question three times, you might get three different answers. Because of this unpredictability, we cannot use standard tests alone. Instead, we use a process called **"Evals"** (short for evaluations). 

This guide explains how to test AI agents, what the results actually mean, and the statistics behind them. 

## Contents

- [1. The Core Vocabulary](#1-the-core-vocabulary)
  - [Measuring Success: `pass@k` vs. `pass^k`](#measuring-success-passk-vs-passk)
- [2. Standard Tests vs. Evals: Why We Need Both](#2-standard-tests-vs-evals-why-we-need-both)
- [3. What an Eval Result Actually Proves (And What It Doesn't)](#3-what-an-eval-result-actually-proves-and-what-it-doesnt)
  - [A. You Are Testing the Whole System, Not Just the AI](#a-you-are-testing-the-whole-system-not-just-the-ai)
  - [B. A Small Number of Trials Proves Almost Nothing](#b-a-small-number-of-trials-proves-almost-nothing)
  - [C. It Only Covers One Specific Scenario](#c-it-only-covers-one-specific-scenario)
  - [D. Evals Do Not Replace Real Security Controls](#d-evals-do-not-replace-real-security-controls)
- [Appendix: The Math Behind the "Rule of Three"](#appendix-the-math-behind-the-rule-of-three)

---

## 1. The Core Vocabulary

To understand evals, you need to know these standard terms (based on [Anthropic's definitions](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)):

*   **Task:** A single test scenario. It includes the input you give the AI and the rules for what counts as a "success."
*   **Trial:** One single attempt at a task. Because AI answers vary, an eval will run multiple trials of the same task.
*   **Grader:** The automated logic that checks if the trial was successful (e.g., a script that checks, "Did the AI include the correct folder name?").
*   **Transcript:** The complete log of what happened during the trial, including the AI's thoughts, tool uses, and final output.
*   **Outcome:** The final state of the environment at the end of a trial.
*   **Evaluation Harness:** The testing software that automatically runs the trials and graders. (Do not confuse this with the *agent harness*, which is the code that actually runs your app).

### Measuring Success: `pass@k` vs. `pass^k`

When running multiple trials (let's call the number of trials $k$), there are two ways to grade the AI:

*   **`pass@k` (At least one success):** The test passes if the AI gets it right *at least once* out of $k$ trials.
*   **`pass^k` (Total success):** The test passes only if the AI gets it right *every single time* in all $k$ trials.

**Security Warning:** For security testing, **always use `pass^k`**. Attackers will try an attack over and over again. If your system blocks the attack 9 times but lets it through on the 10th try, `pass@k` would call that a success, hiding a massive security flaw.

---

## 2. Standard Tests vs. Evals: Why We Need Both

A secure AI agent requires two layers of testing because they answer different questions:

1.  **Standard Tests (Control Tests):** These are normal, predictable tests. They prove that your security rules work even if the AI goes rogue. For example, if the AI tries to use a tool that is not on the allow-list, the code will block it 100% of the time. 
2.  **Evals (Behavior):** These measure how the AI *behaves* in different scenarios. For example, if a hacker hides a malicious instruction in a document, an eval tests how often the AI gets tricked into following it. 

The controls are your strong shields; the evals simply measure how often the AI actually hits those shields.

---

## 3. What an Eval Result Actually Proves (And What It Doesn't)

Imagine you run an eval with 3 trials to see if your agent resists a hacker's prompt injection. All 3 trials pass perfectly. It is tempting to think your system is 100% secure. However, that result proves a lot less than you might think, for four reasons:

### A. You Are Testing the Whole System, Not Just the AI
An eval tests your agent harness, your system prompt, and the AI model all working together. If it passes, it means *this specific combination* resisted the attack. If you change a single sentence in your system prompt, or upgrade to a new version of the AI, you must run the eval again.

### B. A Small Number of Trials Proves Almost Nothing
In statistics, there is a concept called the ["Rule of Three"](https://en.wikipedia.org/wiki/Rule_of_three_(statistics)). It tells us the highest possible failure rate that could still be hiding behind a perfect test score. 

If you run $n$ trials and they all pass (zero failures), the hidden failure rate could still be as high as about $3/n$ when $n$ is large (with 95% confidence).

Look at this table to see why 3 trials are not enough:

| Trials run (all passed perfectly) | Real-world failure rate could still be... |
| :--- | :--- |
| **3 trials** | Up to **$63\%$** |
| **10 trials** | Up to **$26\%$** |
| **30 trials** | Up to **$10\%$** |
| **60 trials** | Up to **$5\%$** |

To confidently say "our agent resists this attack 95% of the time," you need to run about 60 perfect trials, not just 3.

### C. It Only Covers One Specific Scenario
Your eval only proves that the AI resisted *that exact attack text*. If the attacker changes their wording slightly, the AI might fail. A new wording is a completely new test.

### D. Evals Do Not Replace Real Security Controls
Because evals only show probabilities, they are never a substitute for hard-coded security rules. You must always design your system assuming the AI will eventually be fooled. Evals just tell you how often that fooling might happen.

---

## Appendix: The Math Behind the "Rule of Three"

If you are curious about where the statistics in Section 3 come from, here is the math:

1. **The chance of zero failures:** If a test has a real-world failure rate of $p$, the mathematical chance that $n$ trials will *all* pass in a row is $(1 - p)^n$.
2. **The 95% upper bound:** We want to find the worst-case failure rate ($p$) where there is only a $5\%$ chance of getting a perfect score by pure luck. So, we set the equation to $0.05$: 
   $$(1 - p)^n = 0.05$$
   Solving for $p$ gives us the exact bound: 
   $$p = 1 - 0.05^{1/n}$$
3. **The approximation (Rule of Three):** To make this easier to calculate in your head, we can use natural logarithms ($\ln$):
   $$n \times \ln(1 - p) = \ln(0.05) \approx -3$$
   For small values of $p$, the value of $\ln(1 - p)$ is roughly equal to $-p$. 
   So, $-n \times p \approx -3$, which simplifies to:
   $$p \approx 3/n$$

*Note: This approximation only works well when $n$ is large (greater than 30). For very small numbers, like $n=3$, the formula $3/n$ gives $100\%$, while the exact math gives $63\%$.*