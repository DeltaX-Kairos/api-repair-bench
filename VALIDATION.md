# Public package validation

September 11, 2026. Source update verified against the public live Worker.

- 89 Python tests passed, including custom-function validation, visitor/job isolation, durable state and the reviewed SDK constructor checks. Those constructor checks forbid network requests.
- 27 portable JavaScript core/REST tests passed, including exact Sandbox wire shapes, single dispatch, output bounds, strict JSON, binding checks and independent comparison.
- Recorded and live UI interaction checks passed. Live checks cover custom inputs, one start request, bounded phase execution, same-run polling, uncertain-response handling, reload recovery and explicit next-run preparation.
- The no-key recorded replay and its historical 16-attempt results remain separate from the new live route.
- A real public Invoice repair run completed 4/9 initial checks, then 9/9 after one correction, with no deployment. The generated source, trace and check results were inspected in the browser.
- The updated package includes the reviewed live source and portable transport. The scan found no personal filesystem paths, credential-shaped tokens, private output directory, Sites account metadata or symlinks. Git repository metadata is excluded from this content count.

The earlier published revision was verified from a fresh public clone at `bbb85a76038a49b52370c59c40757b991dea8cba`, with its then-current 71 tests. This source update supersedes that revision and is being synchronized to the public repository.

The public verification used one bounded model-and-Sandbox run. Optional dependencies were already installed at the reviewed pins. Credentials and private Site account metadata were not copied into this source update.
