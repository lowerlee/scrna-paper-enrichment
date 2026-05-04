# Haiku-Only Classification: Design Document

A specification for replacing the regex keyword filter + Claude classifier with a single Haiku-based classifier as the only decision point in the pipeline.

## Why replace the keyword filter

The current two-stage design — regex pre-filter, then Claude — exists for one reason: cost control on the LLM step. Every stage is justified by the question *"is this saving enough money to be worth the complexity?"* At your volume (~50 papers/day), the answer turns out to be no.

Three things drive that conclusion:

**Haiku 4.5 pricing makes per-paper cost effectively zero.** At $1 per million input tokens and $5 per million output tokens (https://www.anthropic.com/claude/haiku), a classification call with ~1500 input tokens (system prompt + abstract) and ~100 output tokens costs $0.0020. Fifty papers a day is $0.10. A month is $3. The "money saved by the regex filter" is rounding error.

**The regex filter has a real failure mode the LLM stage does not.** A methods paper with unusual phrasing ("Here we introduce..." instead of the regex-friendly "We present...") gets dropped before Claude ever sees it, and that drop is invisible in the daily digest. There is no automatic recovery. This is exactly the silent-failure mode the validation section of the README flags as the most dangerous in the pipeline.

**The regex filter is the largest source of design complexity in the pipeline.** Vocabulary curation, weight tuning, threshold calibration, and a whole second validation metric (keyword filter recall) all exist solely to make the pre-filter trustworthy. Removing it removes all of that.

The argument for keeping regex would be: at scale (thousands of papers per day, multiple sources), API cost becomes meaningful and a cheap pre-filter pays for itself. That's a real argument *if* the pipeline grows. As a daily personal digest pulling from one source, it doesn't apply.

## What the new pipeline looks like

Five stages instead of six, with the keyword scorer removed and the classifier called on every deduplicated paper:

1. **Fetch** — bioRxiv API, same as before.
2. **Deduplicate** — DOI check against `data/pipeline.db`, same as before.
3. **Classify** — Haiku 4.5 call per paper, returning structured JSON.
4. **Write digest** — same as before; main section + borderline section.
5. **Log** — same as before, minus keyword-filter counters.

The deletions from the current README structure: `pipeline/keyword_filter.py` and `vocabulary.py` are gone. The `pipeline/classifier.py` module stays but is now called on every paper rather than on filter survivors. The validation script loses one of its three measurements (keyword-filter recall) and gains one (Haiku-vs-Sonnet agreement, optional).

## The classifier prompt

This is the heart of the system, and the part you should write yourself rather than delegate. Claude Code can iterate on the prompt later, but the initial version should encode your judgment about the include/exclude boundary because you understand the domain better than any model can guess.

The prompt has four jobs: state the criteria unambiguously, give the model an explicit decision rule for ambiguous cases, show worked examples that probe the actual decision boundary, and specify the output format strictly enough that JSON parsing doesn't fail.

### Recommended structure: system + user message

The system prompt carries the rules and examples (stable across all calls, cacheable). The user message carries the paper-specific content (title + abstract). This split matters for two reasons: prompt caching reduces cost on the system portion to 10% of the standard rate after the first call, and structurally separating "rules" from "data" is the standard pattern that works well empirically.

### System prompt (full text)

```
You are a classifier for a daily digest of methodology papers in single-cell
genomics and related omics technologies. Your job is to decide whether a paper
develops, optimizes, benchmarks, or builds computational tools for these
technologies — or whether it merely applies them to answer biological questions.

INCLUDE if the paper's primary contribution is one of:
- A new algorithm, tool, or software package for scRNA-seq or omics analysis
- An optimization of an existing computational method (speed, accuracy, scalability)
- A benchmarking study comparing tools or methods
- A new experimental protocol or sequencing technology
- A new statistical model designed for omics data
- A new atlas-scale omics dataset (the dataset itself is the contribution)

EXCLUDE if the paper:
- Uses scRNA-seq or another omics technology as a measurement tool to study
  a gene, disease, tissue, or cell type
- Has a biological finding as its primary contribution, even if it uses
  sophisticated methods
- Is a clinical paper that happens to use sequencing data
- Is unrelated to single-cell or omics technologies

DECISION RULE for ambiguous cases:
Ask yourself: would this paper be cited primarily for the tool/method/dataset
it produced, or primarily for the biological finding it reported? If the
former, INCLUDE. If the latter, EXCLUDE. Many papers contain both methodology
and biology; the question is which is the primary contribution.

EXAMPLES:

Example 1 — INCLUDE
Title: "scIntegrate: a scalable benchmark for single-cell data integration methods"
Abstract: "We present scIntegrate, a benchmarking framework that evaluates 14
existing integration methods across 8 datasets spanning 2.3M cells. We introduce
a new metric for batch correction quality and demonstrate that no single method
dominates across all dataset characteristics..."
Verdict: RELEVANT
Reason: Explicit benchmark with new evaluation metric; the contribution is
methodological.

Example 2 — INCLUDE (atlas, harder case)
Title: "A single-cell transcriptomic atlas of the human kidney across development"
Abstract: "We profile 1.2M cells across 47 human kidney samples spanning fetal
to adult stages, releasing the data and an interactive browser. We identify
several previously uncharacterized cell states..."
Verdict: RELEVANT
Reason: Atlas-scale dataset is the primary contribution; biological findings
are described but the citation will be for the resource.

Example 3 — EXCLUDE (hard negative, application paper)
Title: "Single-cell analysis reveals a novel macrophage population in pancreatic cancer"
Abstract: "We performed scRNA-seq on tumor samples from 24 patients with
pancreatic ductal adenocarcinoma. We identified a novel macrophage population
characterized by high expression of TREM2 and SPP1, and show this population
correlates with poor prognosis..."
Verdict: NOT_RELEVANT
Reason: scRNA-seq used as measurement tool; primary contribution is
the biological finding about macrophages.

Example 4 — EXCLUDE (off-topic)
Title: "Structural basis for LDL-receptor recognition by VSV glycoprotein"
Verdict: NOT_RELEVANT
Reason: Structural biology, unrelated to single-cell or omics technology.

OUTPUT FORMAT:
Respond with valid JSON only, no preamble, no code fences:
{
  "verdict": "RELEVANT" | "NOT_RELEVANT",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "reason": "<one sentence, max 25 words>"
}
```

### User message (per-paper)

```
Title: <paper title>

Abstract: <paper abstract>
```

That's it. No additional framing — the system prompt does the framing, and adding more here just adds tokens.

### Why each piece matters

The **explicit include/exclude lists** mirror the README, so when you update the README you can update the prompt in lockstep and version both together.

The **decision rule** is the single most important sentence. Without it, Claude has to infer from examples how to handle papers that contain both methodology and biology, and inference from few examples is unreliable. The "primarily cited for" framing is operational — it forces a specific kind of judgment rather than a vague impression.

**Four examples, not two.** The standard advice is 2–3 examples; for this task four is right because there are two distinct hard cases (atlas papers as positives, application papers as negatives) plus an obvious-positive and obvious-negative anchor. Two examples cover only two of the four cells in the (positive/negative × easy/hard) matrix.

The **JSON output spec** uses a strict schema with no optional fields. "No preamble, no code fences" is a direct instruction because models, including Haiku, occasionally wrap JSON in ```json fences when not told otherwise, which then breaks `json.loads()`. The 25-word cap on the reason keeps output costs predictable and makes the digest readable.

## API call sketch

A minimal classifier function. Each line is annotated; you would put this in `pipeline/classifier.py`.

```python
import json                                  # for parsing the model's JSON output
from anthropic import Anthropic              # official Anthropic SDK

# Single client instance, reused across calls. The SDK reads ANTHROPIC_API_KEY
# from the environment if no key is passed explicitly.
client = Anthropic()

# Load the system prompt from a versioned file (see "Prompt versioning" below).
# Reading from disk once at module load avoids re-reading per call.
with open("prompts/classifier_v1.txt") as f:
    SYSTEM_PROMPT = f.read()

PROMPT_VERSION = "v1"                        # also stored in DB alongside verdict

def classify(title: str, abstract: str) -> dict:
    """Classify one paper. Returns dict with verdict, confidence, reason,
    and the prompt version used."""

    # The user message is just the title and abstract; the system prompt
    # carries all the rules. cache_control on the system block tells the
    # API to cache it after the first call, dropping subsequent calls'
    # system-prompt cost to 10% of standard rate.
    response = client.messages.create(
        model="claude-haiku-4-5",            # current Haiku model ID
        max_tokens=200,                      # plenty for a one-sentence reason
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"Title: {title}\n\nAbstract: {abstract}",
            }
        ],
    )

    # response.content is a list of content blocks; for a non-tool-using
    # call the first (and only) block is text. .text gives us the raw
    # string the model produced, which should be JSON.
    raw = response.content[0].text

    # Parse. If parsing fails, the caller is responsible for retry logic
    # (see "JSON parse failures" below).
    parsed = json.loads(raw)

    # Attach the prompt version so the database row records what definition
    # was in force when this verdict was made. Critical for retrospective
    # analysis after prompt changes.
    parsed["prompt_version"] = PROMPT_VERSION

    return parsed
```

A few subtleties worth pulling out:

The **`cache_control` block** turns on prompt caching for the system prompt. Without it, every call pays full input rates on the ~1500-token system prompt. With it, only the first call of each 5-minute window pays full rate; subsequent calls pay 10% (https://docs.claude.com/en/docs/build-with-claude/prompt-caching). At 50 calls/day this is a real saving — closer to $1/month total instead of $3.

The **`max_tokens=200`** is a hard cap on output. The expected output is ~50–100 tokens (a short JSON object with a one-sentence reason); 200 leaves headroom for slightly longer reasons without inviting the model to ramble. Setting this too high doesn't increase cost (you pay for actual output, not the cap), but it's a useful safety rail against runaway output.

The **`json.loads(raw)`** call is the one place this can fail. Models occasionally produce JSON-with-prose ("Here is the classification: {...}") despite the instruction to do otherwise. Wrap this in try/except, and on failure either retry once with a stricter follow-up message or log the raw output and skip the paper. Don't silently drop — you want to know how often this happens.

## Cost analysis

Concrete numbers for your volume, using the current Haiku 4.5 rates from https://www.anthropic.com/claude/haiku.

Per call, assuming 1500 input tokens (system + user) and 100 output tokens:

- Without caching: 1500 × $1/M + 100 × $5/M = $0.0015 + $0.0005 = **$0.0020 per call**
- With caching (after first call in a 5-min window): 100 × $0.10/M (cached system) + ~200 × $1/M (uncached user) + 100 × $5/M = roughly **$0.0007 per call**

At 50 papers/day:

- Uncached worst case: $0.10/day, ~$3/month
- With caching: ~$0.04/day, ~$1.20/month

Either way, the cost is small enough that optimization is premature. The reason to use caching anyway is that it costs nothing to add (one field in the API call) and makes the cost story even more obviously a non-issue.

## Optional: tiered escalation

If you want the architecture to scale to higher volumes or if you want a hedge on hard cases, you can add Sonnet (or Opus) as a confirmation step for papers Haiku is uncertain about. There are two reasonable ways to identify "uncertain":

**Self-reported confidence.** Trust Haiku's `"confidence": "LOW"` field and re-run those calls on Sonnet. Simple to implement, but as the README notes (citing Tian et al. 2023, "Just Ask for Calibration"), LLM self-reported confidence is often poorly calibrated. You may find that LOW-confidence verdicts are no less accurate than HIGH ones, in which case this tier accomplishes nothing.

**Two-call agreement.** Run Haiku twice with `temperature=0.7` (instead of the default deterministic setting). Accept the verdict if both runs agree. Escalate to Sonnet if they disagree. This is more robust because disagreement under sampling is an empirical signal of uncertainty rather than a self-report — it correlates with actual ambiguity in a way self-reported confidence often doesn't. The cost is two Haiku calls per paper instead of one, which roughly doubles the total but keeps you under $10/month.

For your volume, I'd recommend: start with **single-call Haiku, no escalation**. Once you have ground truth and have measured Haiku's actual error rate, decide whether escalation is worth adding. Adding it preemptively without data is the kind of complexity that's hard to remove later.

## Validation changes

Two of the three quantities your README's `validate.py` measures still apply, one becomes new:

1. **End-to-end precision and recall** — unchanged. Run the labeled test set through the classifier, compare verdicts to ground truth.
2. **Keyword filter recall** — *deleted*, because there is no keyword filter.
3. **Per-confidence-bucket precision** — unchanged in form, but more important now because confidence is the only triage signal in the borderline section.
4. **(New) Haiku-vs-Sonnet agreement** — optional, only if you implement the escalation tier. Run both Haiku and Sonnet on the test set and report the rate of disagreement, broken down by paper category and difficulty. Disagreement on hard negatives is the most diagnostic; if Haiku and Sonnet agree on those, Haiku alone is probably enough.

The validation script is shorter overall — fewer stages to test, fewer metrics to compute, fewer failure modes to chase.

## Prompt versioning

Keep the prompt in `prompts/classifier_v1.txt` as a plain text file. When you change it, increment to `v2.txt` and update the `PROMPT_VERSION` constant in `classifier.py`. Every classification result in SQLite stores the version it was made under.

This matters because prompt changes are the most common source of behavioral drift in LLM pipelines, and without versioning you can't tell whether a sudden uptick in NOT_RELEVANT verdicts reflects (a) a worse prompt, (b) a bad day at bioRxiv, or (c) actual changes in the field. With versioning, you can re-run the test set against any historical prompt and quantify the impact of each change.

## Pitfalls specific to LLM-only classification

**Prompt drift.** Without a regex stage, the prompt is the entire decision logic. Small edits compound. Always re-run validation after editing the prompt, and never edit the prompt and the test set in the same change — you can no longer tell which one moved the metrics.

**JSON parse failures.** With Haiku at this size, malformed JSON is rare but not zero. Log every parse failure with the raw output. If failures cluster around specific abstract content (long abstracts, abstracts in non-English fragments, abstracts with embedded JSON-like structures), the prompt may need a stricter "JSON only, no exceptions" reminder. Don't silently retry — log first, then retry, so you can measure how often this happens.

**Rate limits.** At 50 calls/day rate limits are not a concern, but if you ever batch backfill (e.g., classifying a year of historical bioRxiv papers in one run), you'll hit them. The Anthropic SDK handles 429 retries automatically; for very large jobs the Batch API gives a 50% discount and is the right tool.

**Calibration drift across model versions.** When Haiku 4.5 is eventually superseded (Haiku 5, etc.), behavior on borderline cases will shift even if the prompt is unchanged. Pin the model ID in `classifier.py` (`claude-haiku-4-5`, not a generic alias) and treat a model upgrade as a full re-validation event, not a transparent improvement.

## Migration checklist

If you're swapping the regex pipeline for this design:

1. Delete `pipeline/keyword_filter.py` and `vocabulary.py`.
2. Move the classifier call from "after keyword filter" to "after deduplication" in `run_pipeline.py`.
3. Replace the existing classifier prompt with the system prompt above (in `prompts/classifier_v1.txt` if not already versioned).
4. Update `classifier.py` to use Haiku 4.5 (`model="claude-haiku-4-5"`) and add the `cache_control` block on the system message.
5. Update `validate.py` to delete the keyword-filter recall test and keep the end-to-end + per-confidence tests.
6. Update the README: remove the Keyword Scoring section, update the Pipeline Stages section to five stages instead of six, update Project Structure to drop the deleted files.
7. Run validation against the test set. The end-to-end precision/recall numbers are now the only metrics that gate enabling the daily timer.

The change is mostly subtractive, which is its main appeal. Less code, fewer concepts, one decision-maker, one place to look when something goes wrong.
