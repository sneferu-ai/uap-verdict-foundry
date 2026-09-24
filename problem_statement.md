# Domain-pack overlay: business_pipeline (v0.1)

Business idea pipeline pack. Reframes every cast role around
discovering, validating, and committing to *real businesses an
operator can ship and sell* — not products in the abstract.

Scope:
  IN:    bootstrap-feasible businesses for the declared operator
         (solo to small_team, time/money budgets honored from B0)
  OUT:   $100M-VC-backed plays, regulated industries the operator
         hasn't cleared, anything in operator's forbidden list

This pack does NOT generate businesses in a vacuum — it ALWAYS reads
the operator intent contract first (runs/<id>/intent_contract.yaml).
Without that contract, B3/B4/B5/B6 silently default to "solo /
balanced" assumptions that often miss the operator's real constraints.

## Framing for adversarial review

You are reviewing a business idea pitch. The operator has declared their
intent contract (goal, team capability, time/money budget, forbidden
models, walk-away criteria) — that contract is the immutable frame.

Your job is adversarial: find the gaps the author missed.

Hard veto signals — flag any of these as critical:
  1. Forbidden business model (operator declared "no MLM, no crypto,
     no adtech, no surveillance capitalism" — author proposed one)
  2. Time-budget violation (proposed scope > operator's declared weeks)
  3. Money-budget violation (proposed pre-launch spend > capital)
  4. Skill mismatch (proposed work the operator can't do or hire for)
  5. Walk-away trigger (idea matches an explicit walk_away_criterion)
  6. No distribution path named (or proposed channel has CAC > LTV)
  7. No buyer named (vague "SMBs" / "creators" / "enterprises")
  8. Slop tells in pitch copy (revolutionize, disrupt, AI-powered, etc.)

Specific judgment calls to push back on:
  - "Universally appealing" → who SPECIFICALLY pays for this?
  - "Highly defensible" → what's the moat? incumbent retaliation cost?
  - "Easy to monetize" → what's the price point and who set the comp?
  - "Quick to build" → what's the riskiest unknown? art? mechanic? channel?
  - Any feature that requires the operator to hire (when team_capability
    is "solo" or "duo") → flag as scope violation

The author has skin in their pitch. You don't. Be specific, be cited,
be unkind to the work but kind to the author. Cite the operator's
intent contract by field when you flag a violation — that's the
evidence that turns a critique from opinion into receipts.

## Anti-patterns the adversary will hunt for

- Hand-waving the customer ('SMBs need this') instead of naming a specific buyer with a budget line and an existing workflow
- Forgetting distribution (the idea is 'great' but the founder has no channel and the proposed channel costs $500 CAC for $10 LTV)
- Ignoring the operator's forbidden list (proposed an MLM/crypto/adtech play when the contract explicitly forbade it)
- Time-budget violation (proposed an 18-month build for an operator declaring 6 weeks of capacity)
- Money-budget violation (proposed a $200k pre-launch spend for an operator declaring $5k of capital)
- Skill mismatch (proposed an iOS-native play for an operator whose existing_skills_assets list is web/Python only)
- Walk-away violation (proposed an idea that triggers an explicit walk_away_criterion the operator wrote)
- Slop tells in pitch copy (em-dashes, 'revolutionize', 'disrupt', 'AI-powered' as the differentiator, 'unlock value', 'leverage synergies')
- Genre rip-off masquerading as novelty (you said 'Notion for X', you actually proposed Notion for X with no defensible difference)

---

## Operator's seed

UAP photograph/video decomposition and analysis tool — with a xenoscience story foundry built on top

I need to build a UFO/UAP photograph/video decomposition and analysis tool.

Use the Sneferu SDK / system to give a yes or no on "is this a UAP" — and the reasons why or why not.

If it IS a UAP: get science. Do research runs. Invent the technology that would be needed to do what the object is doing, and how you'd use it. Then tell us about the planet it came from.

So this will be part reality, part fantasy. Mostly reality.

This builds up a case log of stories. I want comic book stories and animations out of it, and maybe games. A secondary product might be a game based on the planets. Who knows.

**First science.**

Intake: upload the image, where it was taken, and all the MUFON UFO reporting info.

Then the system that gets built on that should be the best "this is what is in this image" system that has ever been built.

On the Sneferu integration: this is an `sdk` product. That means a running Sneferu engine sits behind it — the product drives claudopus through the Python SDK to do the actual research runs, ideation and cross-model judging, rather than reimplementing any of that. Assume the engine is there and available at runtime; spec how the product uses it. 

Fun idea: design redering of the objects based on physics and the alinets too. use the graphcis pipleine. that wol dbe impressive. 

ill set up 20 + coding round so we can do it all on the first build.