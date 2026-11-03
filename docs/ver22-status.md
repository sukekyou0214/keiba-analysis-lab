# Ver.22 implementation status — 2026-11-03

This change adds an owner-password-gated Elizabeth II workspace while preserving the existing app and Ver.21 research page. It is an acquisition/analysis foundation, not a completed, live-verified 2026 prediction model.

Implemented:
- Wrong-race/year checkpoint rejection in the Ver.21 collection, calculation, result display and ZIP restore paths.
- Separate historical and 2026 entrant checkpoints, SQLite transactions, save after every horse, resume successful horses, sequential two-second pacing and stop on first failed horse.
- Three-section mobile workspace; source time, tentative entrant/jockey labels, explicit missing popularity.
- Strict pre-target historical features: previous race, finish, margin, closing 3F, turf distances 1800/2000/2200/2400, Kyoto, fourth-corner style, and source-labelled S/M/H only.
- Kyoto and Hanshin summaries separated; all ten years/full horse manifest required for summary display. Popularity bands and historical win return; missing place payouts remain missing. Sample filters and independent exploratory race-affinity calculation.
- Transparent provisional comparison score with evidence coverage. It is not an ability index or calibrated win probability; value candidates require observed tentative popularity.

Operational boundaries:
- netkeiba terms (https://netkeiba.com/info/kiyaku.html), Article 14, restrict making acquired data available to unspecified/many people. No provider dataset is committed. V22 requires an operator-configured `V22_ACCESS_PASSWORD` in Streamlit Secrets; do not put it in git or share it publicly. Existing public legacy data flows are unchanged by this patch and are not certified compliant by this implementation.
- This development executor returned HTTP 400 with an empty response for the special-page request. No full Elizabeth historical cohort was acquired or validated here. No access controls were bypassed.
- Latest searched entry page: https://race.netkeiba.com/race/shutuba.html?race_id=202608050411 . It showed 23 tentative entries and missing popularity on 2026-11-03; no invented odds or final entrants.
- Default SQLite survives sessions on the same host, not Streamlit container replacement. Set `KEIBA_V22_STORE` to a durable mounted path or use ZIP checkpoints. No durable volume has been provisioned.
- Sustained-power/burst/exhaustion labels, historically validated pattern-match score, and statistical expected value remain pending source lap/closing-rank data and independent validation. UI explicitly marks them pending.
- The source S/M/H labels are optional and are not filled from simplistic first/last 3F differences. Missing source labels remain missing.

Validation: local unit and Streamlit AppTest regression suite; GitHub Actions runs the same tests. Real Streamlit initial launch checked before implementation; post-merge public gate verification to be recorded separately. All fixtures in tests are synthetic.
