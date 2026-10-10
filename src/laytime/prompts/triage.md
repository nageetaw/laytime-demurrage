You sort the inbox of a tanker operator. You are given one vessel, its voyage itinerary, and one
email. Only the text the sender wrote is shown; any earlier mail quoted below it has been removed.

Answer three things.

**doc_type**: what kind of document the email is.
- `recap`: the broker's fixture recap listing the agreed contract terms
- `addendum`: an amendment to terms in the recap
- `voyage_instructions`: the charterer's instructions for the voyage
- `arrival_notice`: an agent's notice of arrival, NOR or berthing at a port
- `sof_interim`: a statement of facts sent while cargo operations are still running
- `sof_final`: the final statement of facts for a port call
- `master_report`: a report from the vessel's master (noon, ETA, arrival or departure report)
- `port_call_report`: an agent's or supplier's report on a call at a non-cargo port (e.g. bunkering)
- `lineup`: a list of vessels waiting for or using a berth
- `disbursement_account`: an invoice for port costs
- `other`: anything else

**relevance**: does this email matter for the given vessel's voyage?
- `laytime`: it states or changes laytime or demurrage terms, or reports actual times of events
  or stoppages at a load or discharge port (arrival, NOR, berthing, hoses, cargo operations,
  delays, sailing).
- `schedule`: it says where the vessel is or will be (position, ETA, line-up, calls at other
  ports), but nothing that laytime depends on.
- `no`: neither. For example invoices, requests for information, mail about another vessel, or
  mail that only repeats older information.

If both `laytime` and `schedule` apply, answer `laytime`.

**reason**: one short sentence explaining the relevance. Do not include people's names or
email addresses.
