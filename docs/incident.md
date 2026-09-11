# Incident: the 200 OK that dropped page 2

An API client returned HTTP 200, so the integration looked healthy. The caller still saw only the first page because the client returned `api.fetch(None)["items"]` and never followed `next`.

| Review point | Silent behavior | Contract behavior |
|---|---|---|
| Page 2 | Dropped after a successful 200 | Follow `next` until it is `None` |
| Empty page | Treated as the end | Continue when `next` is present |
| Duplicate item | Easy to accidentally deduplicate | Preserve every occurrence in order |
| Cursor loop | No stopping rule | Raise `ContractError('repeated pagination cursor')` before a repeat |

That is the job this desk reviews: a broken API client function when HTTP succeeded and the contract didn’t.
