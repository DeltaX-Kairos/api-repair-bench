# Usability validation: missing-page repair workflow

**Status:** protocol prepared; no sessions conducted or participant findings claimed.

## Research question

Can an integration engineer, without coaching, identify the missing-page failure, run the supplied repair case, interpret its result and distinguish evidence export from deployment?

Recruit **two or three integration engineers** with experience maintaining API clients. Obtain consent for notes or recording before the session. Use fictional data only. This protocol does not authorize outreach or public attribution.

## Session: 15 minutes per participant

Give the participant the public homepage and this task, without explaining the controls:

> A client returns HTTP 200, but the downstream import contains only the first page. Use the supplied pagination case to investigate a repair. Decide whether the output is sufficient to review, and save the evidence you would give a teammate.

Let the participant navigate without assistance for up to five minutes. If live admission is unavailable, record that blockage as a product-access finding; continue with the clearly labelled saved demonstration and record that the remaining observations concern replay, not live use.

## Observe and record

| Observation | Record |
|---|---|
| Understands audience and task | Participant's own description; time to first correct explanation |
| Finds pagination case and starts once | Completed independently / assisted / blocked; elapsed time |
| Reads the result | Which named check or stopping reason they cite |
| Understands bounded correction | Their explanation of when a correction is allowed and when the workflow stops |
| Understands authority | Whether they believe passing checks deploys or merges code |
| Saves evidence | Successful download / cannot locate / blocked |
| Handles uncertainty | Their response to an unavailable, failed or unresolved outcome |

Keep observed behavior separate from interpretation. Do not turn synthetic task completion into reliability or time-saving claims. Report the participant count and whether each session used live execution or replay.

## Ask after the task

1. What problem would you use this for, and what would you do with the output?
2. What evidence would you still need before merging the proposed function?
3. What does the run prove, and what does it leave unproven?
4. Where did the interface make you stop or guess?
5. How does this compare with your current debugging and review workflow?

## Update decisions

Prioritize any misconception that a passing result means deployment, universal correctness or independent execution attestation. Fix navigation if participants cannot identify the supplied case or evidence download. Treat unmet prerequisites and admission failures as findings rather than dropping the session.

Publish a concise anonymized findings note only after the sessions exist, with participant consent and exact sample size. Until then, this document is a study plan.
