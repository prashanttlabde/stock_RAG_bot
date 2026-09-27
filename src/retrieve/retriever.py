"""Retrieval stage: query in, ranked grounded chunks out (architecture.md §7 steps 2-4).

This is the only place a user question meets the vector store. Three decisions
define its behaviour:

1. **Scheme scoping.** `detect_scheme_id` recognises a scheme mention and turns it
   into a Chroma `where` filter, so "exit load of HDFC Large Cap" cannot return the
   ELSS page. A question naming two schemes is left unscoped rather than guessed at.
2. **The similarity floor.** Cosine distance is converted to similarity
   (`1 - distance`) and weak hits are dropped, so an off-corpus question returns an
   empty result instead of the least-bad chunk. The floor is tuned empirically by
   `scripts/tune_floor.py`, not guessed.
3. **Duplicate collapse.** 16 of the 30 stored chunks are byte-identical, because
   Groww prints the same boilerplate on every scheme page. Without collapsing them
   a `Understand terms` question returns one sentence five times under five scheme
   ids, and INV-1 (exactly one citation) forces the generator to pick one anyway.

The model and the collection are both memoised, so a Streamlit session pays the
MiniLM load and the Chroma open once rather than per keystroke.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from chromadb.api.models.Collection import Collection

from src import config
from src.errors import RetrievalError
from src.ingest.embed import embed_query
from src.ingest.store import get_collection
from src.logging_config import get_logger
from src.types import Chunk, RetrievalHit, RetrievalResult

logger = get_logger("retrieve")

SCHEME_ALIASES: dict[str, str] = {
    "large cap": "hdfc-large-cap-direct-growth",
    "large cap fund": "hdfc-large-cap-direct-growth",
    "hdfc large cap": "hdfc-large-cap-direct-growth",
    "elss": "hdfc-elss-tax-saver-direct-growth",
    "tax saver": "hdfc-elss-tax-saver-direct-growth",
    "elss tax saver": "hdfc-elss-tax-saver-direct-growth",
    "80c": "hdfc-elss-tax-saver-direct-growth",
    "80c tax saver": "hdfc-elss-tax-saver-direct-growth",
    "80d": "hdfc-elss-tax-saver-direct-growth",
    "tax": "hdfc-elss-tax-saver-direct-growth",
    "flexi cap": "hdfc-equity-flexi-cap-direct-growth",
    "flexi cap fund": "hdfc-equity-flexi-cap-direct-growth",
    "equity flexi cap": "hdfc-equity-flexi-cap-direct-growth",
    "hdfc equity fund": "hdfc-equity-flexi-cap-direct-growth",
    "small cap": "hdfc-small-cap-direct-growth",
    "small cap fund": "hdfc-small-cap-direct-growth",
    "balanced advantage": "hdfc-balanced-advantage-direct-growth",
    "balanced advantage fund": "hdfc-balanced-advantage-direct-growth",
    "hybrid": "hdfc-balanced-advantage-direct-growth",
}

_PUNCTUATION = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")

# HDFC scheme families that exist as real funds but are NOT in the 5-URL allowlist.
# The corpus is a closed set, so naming one of these is a definitive miss rather than
# a weak match, and it is answered deterministically instead of by a score threshold.
# Phrases are deliberately multi-word where a bare word would collide with the corpus
# vocabulary: "nifty" alone is excluded because the Balanced Advantage chunk literally
# contains "NIFTY 50 Hybrid Composite Debt 50:50 Index" as its benchmark.
OUT_OF_CORPUS_SCHEMES: dict[str, str] = {
    "mid cap": "HDFC Mid-Cap Opportunities",
    "mid-cap": "HDFC Mid-Cap Opportunities",
    "midcap": "HDFC Mid-Cap Opportunities",
    "mini cap": "HDFC Mini Cap",
    "minicap": "HDFC Mini Cap",
    "ultra large cap": "HDFC Ultra Large Cap",
    "floating rate": "HDFC Floating Rate",
    "floater": "HDFC Floating Rate",
    "income fund": "HDFC Income",
    "bond fund": "HDFC Bond",
    "debt fund": "HDFC Debt",
    "liquid fund": "HDFC Liquid",
    "money market": "HDFC Money Market",
    "index fund": "an HDFC index fund",
    "etf": "an HDFC ETF",
    "thematic": "an HDFC thematic fund",
    "pharma": "HDFC Pharma",
    "psu": "HDFC PSU",
    "dividend yield": "HDFC Dividend Yield",
    "savings fund": "HDFC Savings",
    "banking and psu": "HDFC Banking and PSU Bond",
    "value fund": "HDFC Value",
    "consumption": "HDFC Consumption",
    "infrastructure": "HDFC Infrastructure",
    "natural gas": "HDFC Natural Gas",
    "gold fund": "HDFC Gold",
}

# Fund houses and broker platforms outside the allowlist. This tier is separate from
# OUT_OF_CORPUS_SCHEMES because of *how* a miss is proved.
#
# A category word is ambiguous: "mid cap" in "Compare HDFC Mid Cap and HDFC Large Cap"
# is a comparison the corpus can partly answer, so it only counts as a miss when no
# indexed scheme is also named. A competing brand is never ambiguous — "Mirae Asset
# Large Cap" is HDFC's Mirae Asset Large Cap by no reading, so naming it is a miss
# regardless of which indexed alias the rest of the sentence also contains. Without
# this split, "What is the exit load of Mirae Asset Large Cap?" matched the "large cap"
# alias and confidently answered with HDFC Large Cap's exit load.
#
# Every entry was checked against all 30 corpus chunks with a word-boundary match, so
# none of them can collide with corpus text ("axis" in "axis of growth", "union" in
# "Union Budget", "canara" in "Canara Bank" as custodian are all absent here).
OUT_OF_CORPUS_FUND_HOUSES: dict[str, str] = {
    "mirae asset": "Mirae Asset",
    "parag parikh": "Parag Parikh",
    "axis": "Axis Mutual Fund",
    "kotak": "Kotak Mutual Fund",
    "sbi": "SBI Mutual Fund",
    "nippon": "Nippon India Mutual Fund",
    "icici prudential": "ICICI Prudential Mutual Fund",
    "icici": "ICICI Mutual Fund",
    "motilal oswal": "Motilal Oswal Mutual Fund",
    "motilal": "Motilal Oswal Mutual Fund",
    "canara": "Canara Mutual Fund",
    "union": "Union Mutual Fund",
    "quant": "Quant Mutual Fund",
    "tata": "Tata Mutual Fund",
    "bandhan": "Bandhan Mutual Fund",
    "bajaj": "Bajaj Allianz Mutual Fund",
    "invesco": "Invesco Mutual Fund",
    "franklin": "Franklin Templeton",
    "aberdeen": "Aberdeen Asset Management",
    "hsbc": "HSBC Mutual Fund",
    "barclays": "Barclays Mutual Fund",
    "dbs": "DBS Mutual Fund",
    "angel one": "Angel One",
    "zerodha": "Zerodha",
}

# Question words and entity words carry no retrieval signal: every chunk mentions
# "HDFC", "Fund" and the scheme name, so leaving them in would let any question match
# on the entity alone. What is left is what the question is actually *asking about*.
# "manager" counts as an entity: the role is named in the corpus, but the manager's
# biography is deliberately absent from it (Phase 1 dropped it), so a question about
# someone's education or background must not be grounded by the word "manager".
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "of", "for",
    "in", "on", "to", "from", "with", "and", "or", "what", "which", "who", "whom",
    "whose", "when", "where", "why", "how", "do", "does", "did", "done", "i", "me",
    "my", "we", "our", "you", "your", "this", "that", "these", "those", "it", "its",
    "as", "at", "by", "if", "then", "than", "there", "here", "can", "could", "should",
    "would", "will", "shall", "may", "might", "must", "tell", "about", "any", "some",
    "give", "show", "list", "please", "need", "want", "know", "s", "t",
    # Generic time-relative modifiers a user can staple onto almost any factual
    # question ("what is the CURRENT expense ratio", "what's the LATEST NAV").
    # None of them names a fact the corpus could contain, so none should ever be
    # treated as a content word requiring a literal match -- see the "current" bug
    # below for what happens when one is: it can coincidentally word-match an
    # unrelated sentence and win the ranking outright.
    "current", "currently", "latest", "recent", "recently", "now", "today", "existing",
    "present",
}
_ENTITY_TERMS = (
    {
        "hdfc", "fund", "funds", "mutual", "scheme", "schemes", "plan", "growth",
        "direct", "option", "elss", "tax", "saver", "equity", "flexi", "cap", "large",
        "small", "balanced", "advantage", "hybrid", "manager", "managers",
    }
    | set(SCHEME_ALIASES)
)

# "fund", "tax", "scheme" stay in _ENTITY_TERMS (i.e. stripped) rather than carved
# back out as content words: every corpus chunk mentions "fund", and ELSS chunks
# mention "tax" constantly, so with MIN_ANSWER_TERM_MATCH=1 either word alone
# grounds *any* question trivially -- e.g. "SEBI registration number of HDFC Mutual
# Fund?" reduces to just ["...", "fund"] and wrongly passes. A question that strips
# to nothing is not left ungroundable by this: `_is_lexically_grounded` treats an
# empty term list as a no-op and falls back to the similarity floor alone, which is
# the documented, tested behaviour (see implementation-notes.md Phase 4).
#
# "nav" is deliberately NOT in this set, unlike "fund"/"tax": it is a specific field
# name, not a scheme-identity word, and it is confined to the 10 of 30 chunks that
# actually carry a NAV figure (one hero block and one About paragraph per scheme) --
# nothing like "fund"'s presence in every chunk. Stripping it as an entity used to
# leave "What is the NAV?" with no content words at all, which was a documented,
# accepted gap (a wordy but correct answer) until "what is CURRENT NAV of HDFC Small
# Cap Fund Direct Growth?" showed the failure mode is worse than wordy: with "current"
# also removed as a stopword above, that question's content terms would otherwise be
# `[]`, triggering the same all-entity-words fallback that let "Current Fund Manager"
# (which repeats the scheme name six times) outrank the actual NAV sentence (which
# doesn't). Keeping "nav" as a real content word gives the ranking something correct
# to anchor on instead.
#
# Longest alias first, so "balanced advantage fund" is preferred over "hybrid" and
# "80c tax saver" over "tax". Word boundaries keep "tax" from matching "taxation"
# while still matching a bare "tax?" once punctuation is stripped.
_ALIASES_BY_LENGTH: list[tuple[str, re.Pattern[str], str]] = sorted(
    (
        (alias, re.compile(rf"\b{re.escape(alias)}\b"), scheme_id)
        for alias, scheme_id in SCHEME_ALIASES.items()
    ),
    key=lambda item: len(item[0]),
    reverse=True,
)


def normalize_query(query: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace for alias matching."""
    return _SPACES.sub(" ", _PUNCTUATION.sub(" ", query.lower())).strip()


def detect_scheme_id(query: str) -> str | None:
    """Return the single scheme this question names, or `None`.

    `None` covers both "no scheme named" and "more than one scheme named". The
    ambiguous case deliberately falls back to an unscoped search rather than
    picking a winner: a user comparing two schemes gets hits from both, which is
    the truthful answer, whereas guessing one would silently hide half the question.
    """
    normalized = normalize_query(query)
    matched = {scheme_id for _, pattern, scheme_id in _ALIASES_BY_LENGTH if pattern.search(normalized)}
    if len(matched) == 1:
        return matched.pop()
    if len(matched) > 1:
        logger.debug("ambiguous scheme mention, staying unscoped: %s", sorted(matched))
    return None


@lru_cache(maxsize=4)
def _collection(path: str) -> Collection:
    """Memoise the Chroma handle per store path.

    Keyed by path for the same reason `store._client` is: a test that points
    `CHROMA_PATH` at a temporary directory must get its own handle instead of the
    one already open against the real store.
    """
    return get_collection(Path(path))


def _collection_count() -> int:
    path = str(config.CHROMA_PATH)
    try:
        return _collection(path).count()
    except Exception as exc:
        raise RetrievalError(
            f"no vector store at {path}. Run `make ingest` first to build it from "
            f"data/corpus. ({type(exc).__name__})"
        ) from exc


def content_terms(query: str, strip_entities: bool = True) -> list[str]:
    """The question's content words with the entity removed.

    "What is the exit load of HDFC Large Cap?" reduces to `["exit", "load"]`. Those
    are the terms a grounded answer has to contain, and they are what
    `_is_lexically_grounded` checks the retrieved text for.

    `_ENTITY_TERMS` is built from scheme names and aliases, so it also swallows generic
    words that happen to appear in them: `nav`, `fund`, `scheme`, `tax`, `cap`, `large`.
    That is the right trade for grounding -- "the exit load of HDFC Large Cap" must not
    require the retrieved text to say "HDFC" -- but it can consume an entire question,
    leaving nothing to match on. `strip_entities=False` returns the question's own content
    words for callers that need to fall back rather than give up; the default is unchanged
    so retrieval keeps grounding against the strict set.
    """
    return [
        word
        for word in normalize_query(query).split()
        if len(word) > 2
        and word not in _STOPWORDS
        and (not strip_entities or word not in _ENTITY_TERMS)
    ]


def detect_out_of_corpus_scheme(query: str) -> str | None:
    """Return the display name of a named scheme that is not in the allowlist.

    A separate signal from `detect_scheme_id` because the two are opposites: one
    says which of the 5 indexed schemes was named, this says a *sixth* one was.
    """
    normalized = normalize_query(query)
    for phrase, display in sorted(OUT_OF_CORPUS_SCHEMES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(phrase)}\b", normalized):
            return display
    return None


def detect_out_of_corpus_fund_house(query: str) -> str | None:
    """Return the display name of an out-of-corpus fund house named in `query`, else None.

    Unlike :func:`detect_out_of_corpus_scheme` this ignores any indexed scheme the
    question also mentions, because a competing brand is decisive on its own. See
    OUT_OF_CORPUS_FUND_HOUSES for why the two tiers differ.
    """
    normalized = normalize_query(query)
    for phrase, display in sorted(OUT_OF_CORPUS_FUND_HOUSES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(phrase)}\b", normalized):
            return display
    return None


@lru_cache(maxsize=512)
def _term_pattern(term: str) -> re.Pattern[str]:
    """One compiled word-boundary pattern for a single content term.

    Word boundaries are load-bearing. Plain substring matching would accept
    `"ratio" in "operation"`, so a question about the expense ratio would look
    grounded in a chunk that merely contains the word "Registrar" or
    "Corporation" somewhere.
    """
    return re.compile(rf"\b{re.escape(term)}\b")


_TOKEN = re.compile(r"[a-z0-9]+")
# A "y" that turns a noun into an adjective, and no other inflection this corpus needs:
# "risky" is how people ask and "Very High Risk" is how the page answers. Restricting it
# to a y after a consonant leaves "key" intact.
_TRAILING_Y = re.compile(r"(?<=[^aeiou])y$")


def _stem(word: str) -> str:
    """The one inflection this corpus needs, or the word unchanged."""
    return _TRAILING_Y.sub("", word)


def _compound_matches(terms: list[str], text: str) -> set[str]:
    """Terms satisfied by a compound token, so "lump sum" can match "Lumpsum".

    The corpus writes `Min. for Lumpsum` on one line while a user types `minimum lump
    sum` on two, and no word-boundary rule bridges that. Requiring the remainder of the
    token to be *another question term* is what keeps this from degenerating into a
    prefix match, which would let `ratio` match `rationalised` and reintroduce the exact
    bug the word-boundary pattern exists to prevent.
    """
    by_stem = {_stem(term): term for term in terms}
    found: set[str] = set()
    for token in _TOKEN.findall(text.lower()):
        for term in terms:
            if len(term) < 3 or not token.startswith(term):
                continue
            partner = by_stem.get(_stem(token[len(term) :]))
            if partner is not None and partner != term:
                found.update({term, partner})
    return found


def matched_terms(terms: list[str], text: str) -> list[str]:
    """The subset of `terms` that `text` mentions, matched on word boundaries.

    Three ways to match, in order of directness: the whole word, the same word after the
    light stem in `_stem`, and a compound whose parts are both question terms. The second
    and third compare *whole tokens* rather than substrings, so neither can be fooled by
    `ratio` inside `operation` or `Corporation`.

    Public because generation needs the same matching to decide which sentence of a
    chunk answers a question. Keeping one implementation is the point: Phases 4, 5 and
    6 all failed or would fail differently if each grew its own notion of "contains".
    """
    lowered = text.lower()
    stems = {_stem(token) for token in _TOKEN.findall(lowered)}
    compounds = _compound_matches(terms, lowered)
    return [
        term
        for term in terms
        if _term_pattern(term).search(lowered)
        or _stem(term) in stems
        or term in compounds
    ]


def _is_lexically_grounded(terms: list[str], text: str) -> bool:
    """True when the retrieved text mentions something the question actually asked about.

    A no-op for a question with no content terms left after entity removal, e.g.
    "tell me about HDFC Large Cap": there is nothing left to require, so the hit is
    allowed through on similarity alone rather than being rejected by a test that has
    no opinion.

    The required number of matches is `config.MIN_ANSWER_TERM_MATCH` (currently 1)
    rather than a literal, so the grounding policy lives in config beside the floor
    instead of being buried here.
    """
    if not terms:
        return True
    return len(matched_terms(terms, text)) >= config.MIN_ANSWER_TERM_MATCH


def _text_key(text: str) -> str:
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()


def _dedupe_by_text(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    """Keep the best-scoring copy of each distinct text, preserving rank order.

    Groww prints one shared glossary and one shared fund-house blurb across all
    five pages, so those chunks are byte-identical. Returning all of them would
    fill the `TOP_K` budget with a single fact and force Phase 6 to cite one of
    several equally-valid identical sources, which is exactly the ambiguity the
    citation rule is meant to avoid.
    """
    seen: set[str] = set()
    distinct: list[RetrievalHit] = []
    for hit in hits:
        key = _text_key(hit.chunk.text)
        if key in seen:
            continue
        seen.add(key)
        distinct.append(hit)
    return distinct


def retrieve(
    query: str,
    top_k: int | None = None,
    scheme_id: str | None = None,
    similarity_floor: float | None = None,
) -> RetrievalResult:
    """Return the grounded chunks that answer `query`, best first.

    `scheme_id` overrides detection, which is what Phase 7's chat service wants when
    it has already resolved a scheme from context. `similarity_floor` overrides
    `config.SIMILARITY_FLOOR` so `scripts/tune_floor.py` can sweep it without
    mutating global config.
    """
    cleaned = query.strip()
    if not cleaned:
        raise RetrievalError("cannot retrieve for an empty query")

    limit = top_k or config.TOP_K
    floor = config.SIMILARITY_FLOOR if similarity_floor is None else similarity_floor

    available = _collection_count()
    if available == 0:
        raise RetrievalError(
            f"the collection {config.CHROMA_COLLECTION!r} at {config.CHROMA_PATH} is empty. "
            "Run `make ingest` first."
        )

    # A competing fund house is a definitive miss, so it is checked before scheme
    # detection: the rest of the question may also contain an indexed alias
    # ("Mirae Asset Large Cap"), and that must not win.
    brand = detect_out_of_corpus_fund_house(cleaned)
    if brand:
        logger.info(
            "retrieve: question names %s, which is outside the 5-URL allowlist; "
            "returning no hits without searching",
            brand,
        )
        return RetrievalResult(
            query=cleaned,
            hits=[],
            detected_scheme_id=None,
            considered_count=0,
            floor=floor,
        )

    detected = detect_scheme_id(cleaned) if scheme_id is None else scheme_id

    if detected is None:
        missing = detect_out_of_corpus_scheme(cleaned)
        if missing:
            logger.info(
                "retrieve: question names %s, which is outside the 5-URL allowlist; "
                "returning no hits without searching",
                missing,
            )
            return RetrievalResult(
                query=cleaned,
                hits=[],
                detected_scheme_id=None,
                considered_count=0,
                floor=floor,
            )

    where = {"scheme_id": detected} if detected else None

    # Ask for more rows than we intend to return, so collapsing duplicates can still
    # leave a full budget of distinct facts.
    n_results = min(available, max(limit, limit * config.RETRIEVE_OVERFETCH))

    collection = _collection(str(config.CHROMA_PATH))
    try:
        found = collection.query(
            query_embeddings=embed_query(cleaned),
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        raise RetrievalError(
            f"vector search failed for {cleaned[:60]!r} ({type(exc).__name__}: {exc}). "
            "Re-run `make ingest` if the store is corrupt."
        ) from exc

    documents = (found.get("documents") or [[]])[0]
    metadatas = (found.get("metadatas") or [[]])[0]
    distances = (found.get("distances") or [[]])[0]
    considered = len(distances)
    if not (len(documents) == len(metadatas) == considered):
        raise RetrievalError(
            "Chroma returned mismatched result columns "
            f"(documents={len(documents)}, metadatas={len(metadatas)}, distances={considered}). "
            "Re-run `make ingest` to rebuild the store."
        )

    above_floor = 0
    grounded: list[RetrievalHit] = []
    terms = content_terms(cleaned)
    for document, metadata, distance in zip(documents, metadatas, distances, strict=True):
        similarity = 1.0 - float(distance)
        if similarity < floor:
            continue
        above_floor += 1
        if not _is_lexically_grounded(terms, document or ""):
            continue
        metadata = metadata or {}
        grounded.append(
            RetrievalHit(
                chunk=Chunk(
                    chunk_id=str(metadata.get("chunk_id", "")),
                    scheme_id=str(metadata.get("scheme_id", "")),
                    source_url=str(metadata.get("source_url", "")),
                    section=metadata.get("section") or None,
                    fetched_at=str(metadata.get("fetched_at", "")),
                    text=document or "",
                ),
                score=round(similarity, 4),
            )
        )

    distinct_all = _dedupe_by_text(grounded)
    ungrounded = above_floor - len(grounded)
    collapsed = len(grounded) - len(distinct_all)
    distinct = distinct_all[:limit]

    logger.info(
        "retrieve: %d considered, %d above floor %.2f, %d ungrounded, %d duplicates "
        "collapsed, %d returned (scheme=%s)",
        considered,
        above_floor,
        floor,
        ungrounded,
        collapsed,
        len(distinct),
        detected or "none",
    )
    if considered and not grounded:
        logger.info("retrieve: no candidate was both above the %.2f floor and grounded", floor)

    return RetrievalResult(
        query=cleaned,
        hits=distinct,
        detected_scheme_id=detected,
        considered_count=considered,
        floor=floor,
    )
