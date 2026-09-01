# docs

`spatial-toolkit.html` is the source of the **capability atlas** — the deployed agent's tool
inventory, enumerated from the running `agent-api` container rather than written from
documentation. It is published as a private Claude artifact and updated **in place at the same
URL** whenever the tool surface, the delivery contract or the stated limits change; keeping the
source here means the published page is no longer only recoverable from a scratch directory.

To update it: re-enumerate from the running container (do not edit the list from memory — a
regex that assumed every tool name contains an underscore silently missed `regionalize` once),
edit this file, and republish to the existing artifact URL. The family counts in each
`<h2>…<span class="n">N tools</span>` header must match the number of `.tool` entries beneath
it; the standfirst and footer totals exclude `execute_code`.

`agent-notes/` holds findings that are about *how to work on this system* rather than about the
code itself — measured provider behaviour and process lessons that cost a deploy cycle to learn.
They are copies of the agent's own working notes, kept here so they survive outside one machine.

Note that the agent's full working-note set is deliberately **not** mirrored here: this
repository is public, and some notes describe deployment specifics (credential file locations
on a reachable host) that should not be published. Only notes that carry no operational detail
are copied in.
