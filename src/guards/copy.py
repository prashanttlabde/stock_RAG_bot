"""User-facing refusal copy and the educational links it may point at (architecture.md §8).

Every string a refusing turn can show lives here so the wording is reviewable in one
place and can be diffed when it changes. `policy.py` holds no literal message text.

Two rules constrain this module:

1. **A refusal may link outside the corpus; an answer may not.** The 5-URL allowlist
   (INV-1) governs citations in answers. Educational links are the single exception and
   they are permitted only inside a refusal message, never as a citation.
2. **PII refusal copy is constant.** `PII_MESSAGE` contains no interpolation at all, so
   there is no code path that could splice a matched PAN or Aadhaar number into a
   response (INV-3 / FR-6).
"""

from __future__ import annotations

# --- Educational links -------------------------------------------------------------
# The only non-corpus URLs the application is allowed to emit. All three are stable,
# public, regulator- or issuer-owned destinations for the exact situation each refusal
# describes.

AMFI_INVESTOR_EDUCATION_URL = "https://www.amfiindia.com/investor-education"
AMFI_INVESTOR_EDUCATION_LABEL = "AMFI investor education resources"

SEBI_SCORES_URL = "https://scores.sebi.gov.in"
SEBI_SCORES_LABEL = "SEBI SCORES"

HDFC_CONTACT_URL = "https://www.hdfcmf.com/contact-us"
HDFC_CONTACT_LABEL = "HDFC Mutual Fund contact page"

# Used when a returns question does not name a scheme, so no scheme page can be named
# in the refusal. It is deliberately not a URL: there is no single public page listing
# "the 5 schemes this bot covers", and inventing one would be a fabricated citation.
NO_SCHEME_LINK_LABEL = "the list of the 5 scheme pages I cover is in the Sources panel"

# --- Refusal copy ------------------------------------------------------------------

PII_MESSAGE = (
    "I can't help with that. Please don't share PAN, Aadhaar, account numbers, OTPs, "
    "or other personal details here. I don't store anything you type."
)

ADVICE_MESSAGE = (
    "I'm a facts-only assistant, so I can't recommend a scheme or tell you whether to "
    "buy or sell. I can share published scheme facts like expense ratio, exit load, and "
    f"minimum SIP. For guidance, see AMFI's investor education resources: "
    f"{AMFI_INVESTOR_EDUCATION_URL}"
)

GRIEVANCE_MESSAGE = (
    "This looks like a complaint or regulator matter, which I can't handle. Please "
    f"raise it through the AMC's official grievance channel ({HDFC_CONTACT_LABEL}: "
    f"{HDFC_CONTACT_URL}) or SEBI's SCORES portal ({SEBI_SCORES_LABEL}: "
    f"{SEBI_SCORES_URL})."
)

RETURNS_MESSAGE = (
    "I don't compute or compare returns. The scheme's official factsheet on the source "
    "page has the published performance figures: {link}"
)

RETURNS_MESSAGE_NO_SCHEME = (
    "I don't compute or compare returns. Published performance figures live in each "
    f"scheme's official factsheet, and {NO_SCHEME_LINK_LABEL}."
)

# The single persistent disclaimer the UI shows on every load, verbatim from PRD.md
# Appendix A. It lives here, not in app.py, because it is copy, not logic, and
# because `format_reply` (Phase 7) appends it to every factual answer too -- one
# constant, so the README (Phase 10), the UI (Phase 8), and every reply agree by
# construction rather than by three people copying the same sentence correctly.
DISCLAIMER = (
    "Facts-only assistant. Answers are based on public scheme pages and are not "
    "investment advice. Do not share PAN, Aadhaar, account numbers, OTPs, or other "
    "personal data."
)
