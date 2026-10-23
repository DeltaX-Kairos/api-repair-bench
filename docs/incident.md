# Fictional incident: HTTP 200, page two missing

**Audience:** an integration engineer investigating an API client that returns HTTP 200 but silently loses or corrupts data.

**Scenario:** a nightly import reports a successful request. The downstream list is shorter than expected. The client returns `api.fetch(None)["items"]`, so every item after the first page is omitted. HTTP status alone cannot detect the contract failure.

```python
def pagination_bug(api):
    return api.fetch(None)["items"]
```

Paste a function or choose **Pagination repair** in the [live workspace](https://repairbench.deltaxevaluate.com/live/). Nemotron proposes a replacement; Sandbox executes the declared cases; the application compares observations and stops for review, with at most one correction. The engineer inspects the replacement and downloads evidence before deciding whether to use it.

| Contract point | Before: broken or unsafe behavior | Required result |
|---|---|---|
| Missing second page | A successful first response hides later items | Follow `next` until it is `None` |
| Empty intermediate page | An empty `items` list can terminate traversal too early | Continue when `next` is present |
| Duplicate occurrence | A set or deduplication pass silently changes the response | Preserve every occurrence in order |
| Repeated cursor | A loop can repeat a request indefinitely | Raise `ContractError('repeated pagination cursor')` before fetching a cursor twice |
| Request budget | A long chain can consume unbounded requests | Make at most ten fetches; raise `ContractError('request budget exhausted')` before an eleventh |

A matching result means the proposed function matched this declared contract under these checks. It does not prove correctness for every API or authorize deployment. The October 13 recorded hosted pagination run passed **4/4** declared checks on its first proposal; the earlier historical series did not produce a valid pagination patch.

This scenario and its data are **fictional**. They illustrate a plausible integration incident, not a customer outage, field trial, measured time saving or report from an on-call engineer.
