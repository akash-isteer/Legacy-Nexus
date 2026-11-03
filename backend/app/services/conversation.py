"""Conversation context for Code Q&A and Document Q&A.

Pure functions, no database: each module stores its own history in its own database and passes the recent
turns in. Everything here is deterministic, so understanding a follow-up costs no AI call:

1. Detect a follow-up ("why?", "what about CUSTOMER_MASTER?", "who calls it?", "and the second one?").
2. Rewrite it into a stand-alone question where that can be done safely:
     "what about X?"     -> the previous question with its subject replaced by X
     "who calls it?"     -> the pronoun replaced by the subject of the previous turn
3. Search with the rewritten question (or the follow-up plus the previous question), so retrieval finds the
   right code or passages even when the follow-up names nothing.
4. Give the model a short, size-capped summary of the earlier turns, marked as context only, never evidence.

With CONVERSATION_CONTEXT=auto (the default) steps 3-4 apply only to follow-ups: stand-alone questions are
sent exactly as before, so they cost no extra tokens and still hit the answer cache.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FOLLOW_UP_START = re.compile(
    r"^(?:and|also|then|so|but|or|what about|how about|and what about|what else|why|how come|"
    r"same for|same question for|now|ok(?:ay)?|instead)\b", re.I)
PRONOUNS = re.compile(r"\b(it|its|it's|this|that|these|those|they|them|their|theirs|he|she|him|her)\b", re.I)
REFERENCES = re.compile(
    r"\b(above|previous|earlier|last answer|that answer|your answer|you said|you mentioned|the same|same one|"
    r"the first one|the second one|the third one|the last one|the other one|more detail|more details|elaborate|"
    r"explain further|explain more|tell me more|give an example|for example\?|another example)\b", re.I)
WHAT_ABOUT = re.compile(r"^(?:and\s+|ok(?:ay)?,?\s+|so\s+)?(?:what|how)\s+about\s+(?P<x>.+?)\s*\??$", re.I)
SUBJECT_PRONOUN = re.compile(r"\b(it|this|that|them|they|those|these)\b", re.I)


@dataclass
class Turn:
    """One earlier exchange, newest last."""

    question: str
    answer: str
    subjects: list[str] = field(default_factory=list)  # names the answer was about (programs, tables, ...)
    resolved: str | None = None  # how that question was read, if it was itself a follow-up


@dataclass
class ContextPlan:
    question: str
    follow_up: bool
    resolved_question: str | None  # stand-alone rewrite, when one could be made
    retrieval_query: str  # what to search with
    history_text: str  # earlier turns for the prompt ("" = none sent)
    turns_used: int

    def public(self) -> dict:
        return {"follow_up": self.follow_up, "resolved_question": self.resolved_question,
                "turns_used": self.turns_used}


def is_follow_up(question: str) -> bool:
    q = question.strip()
    words = re.findall(r"[A-Za-z0-9_'-]+", q)
    if not words:
        return False
    if FOLLOW_UP_START.search(q) or REFERENCES.search(q) or PRONOUNS.search(q):
        return True
    return len(words) <= 3  # "Why?", "Which table?", "Explain step two"


def _replace_word(text: str, old: str, new: str) -> str | None:
    pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(old)}(?![A-Za-z0-9_-])", re.I)
    if not pattern.search(text):
        return None
    return pattern.sub(new, text, count=1)


def rewrite(question: str, previous: Turn | None) -> str | None:
    """A stand-alone version of a follow-up, or None when it cannot be made safely."""
    if previous is None:
        return None
    base = previous.resolved or previous.question
    m = WHAT_ABOUT.match(question.strip())
    if m:
        new_subject = m.group("x").strip(" ?.!'\"`")
        for subject in previous.subjects:
            swapped = _replace_word(base, subject, new_subject)
            if swapped:
                return swapped
        return None
    if previous.subjects and SUBJECT_PRONOUN.search(question):
        subject = previous.subjects[0]
        return SUBJECT_PRONOUN.sub(subject, question, count=1)
    return None


def history_block(turns: list[Turn], max_chars: int) -> str:
    """Earlier turns, newest kept first when the budget runs out, printed oldest first."""
    if not turns or max_chars <= 0:
        return ""
    lines: list[str] = []
    used = 0
    for t in reversed(turns):
        answer = " ".join(t.answer.split())
        if len(answer) > 400:
            answer = answer[:400].rsplit(" ", 1)[0] + " …"
        item = f"Q: {t.question.strip()}\nA: {answer}"
        if used + len(item) > max_chars and lines:
            break
        lines.append(item[:max_chars])
        used += len(item)
    return ("CONVERSATION SO FAR (use only to understand what the new question refers to; it is NOT evidence):\n"
            + "\n".join(reversed(lines)) + "\n")


def plan(question: str, turns: list[Turn], mode: str = "auto", max_turns: int = 4,
         max_chars: int = 2500) -> ContextPlan:
    """Decide how a question uses its conversation. ``turns`` are the earlier exchanges, oldest first."""
    question = question.strip()
    recent = turns[-max_turns:] if max_turns else []
    mode = (mode or "auto").lower()
    if mode == "off" or not recent:
        return ContextPlan(question, False, None, question, "", 0)
    follow_up = is_follow_up(question)
    if mode == "auto" and not follow_up:
        return ContextPlan(question, False, None, question, "", 0)
    resolved = rewrite(question, recent[-1]) if follow_up else None
    if resolved:
        query = resolved
    elif follow_up:  # nothing to substitute: search with the follow-up plus what it follows
        query = f"{question} {recent[-1].resolved or recent[-1].question}"
    else:
        query = question
    return ContextPlan(question, follow_up, resolved, query, history_block(recent, max_chars), len(recent))


def title_for(question: str) -> str:
    q = " ".join(question.split())
    return q if len(q) <= 80 else q[:80].rsplit(" ", 1)[0] + "…"
