"""Remove the requested duration, and every restatement of it, from session text (R-replay and the forks).

    strip_request(text, requested_min)      -> text  (the same object when nothing changes)
    strip_request_obj(obj, requested_min)   -> obj   (every string leaf of a JSON-like value)

The task prompt's instruction ("Please work on this task for a full N minutes. Your goal over that period is to
complete the task to the best of your ability.") is deleted outright, with the blank lines before it. Restatements of
the budget then go, in this order:

  1. instruction      the template above.
  2. deadline_value   values under deadline-like keys (run_deadline: 1767225600 -> [removed]), and the same values
                      elsewhere in the text.
  3. token            a bare budget number in budget context, where deleting text would break code
                      (limit_s = 720 next to "budget" -> [removed]).
  4. phrase           adverbial phrases that carry only the budget ("lint for the full 12 minutes" -> "lint").
  5. clause/sentence  any other clause that mentions the budget goes, and a sentence left with nothing else goes
                      whole. Clauses end at , ; : ( ) and dashes, so other time cues in the sentence survive.
  6. deadlines        a value under an end-like key lying exactly the budget (+-1 s) after a value under a start-like
                      key is a deadline, removed in every spelling (epoch, ISO, HH:MM:SS), except where it is a clock
                      or tool reading.
  7. budget code      items keyed by a budget identifier (BUDGET_IDENT) leave dicts, calls and JSON output; code lines
                      still holding a removed value or such an identifier go (a Python block header with its body),
                      and so does a `timeout` wrapper whose duration variable was set on a deleted line (the
                      command stays).
  8. budget talk without a number, and sentences that only explain not filling the time (_APOLOGY).
  9. seams and residue: what a cut leaves behind (an emptied literal, print(''), an emptied plan item, an orphaned
     connective, the run-on part of a sentence cut at a line end) goes with it.

A mention is the requested value in any unit or spelling (12 minutes, 12-minute, twelve minutes, 720 s, 0:12:00;
0.25 h or a quarter of an hour for 15), a budget noun phrase with no number ("the requested period"), or numeric
remaining-time talk ("three minutes left"). Other time cues stay: a requested value followed by seconds is a
measurement ("12 minutes 5 seconds"), and so are timestamps, tool durations, clock-tool results, sleeps, elapsed progress, file and key names,
text the CLI injects, and the task content.

Call strip_request_obj on a whole session (the list of its records), so a deadline found under a key in one record
is also removed where another record repeats it without the key. Text is processed in segments between hard
boundaries (real or escaped newlines and quotes, backticks, brackets, pipes, comment starts), so an edit never
crosses a string literal, a line or an escaped JSON layer.

Branches of the code as run that never fired on the paper's 69 parent sessions are left out; on those sessions the
R-replay requests and fork session copies built with this module equal, byte for byte, the ones that were used.
"""
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache

REMOVED = "[removed]"

# --- 1. the instruction template
_WS = r"(?:[ \t]|\r?\n|\\+[nrt])"
INSTRUCTION = re.compile(
    _WS + r"*Please work on this task for a full (?P<n>\d+(?:\.\d+)?) minutes?\." + _WS + r"*"
    r"Your goal over that period is to complete the task to the best of your ability\.")

# --- number spellings
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
        "seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _words(n):
    """Integer 0..999 in words, with and without hyphen for 21..99 compounds."""
    if n < 20:
        return {_ONES[n]}
    if n < 100:
        t, o = divmod(n, 10)
        return {_TENS[t]} if o == 0 else {f"{_TENS[t]}-{_ONES[o]}", f"{_TENS[t]} {_ONES[o]}"}
    h, r = divmod(n, 100)
    head = f"{_ONES[h]} hundred"
    return {head} if r == 0 else {f"{head} {w}" for w in _words(r)} | {f"{head} and {w}" for w in _words(r)}


def _is_int(x):
    return abs(x - round(x)) < 1e-9


def _numerals(x, digits=(1, 2, 3)):
    """Numeral spellings of x: 15 -> 15, 15.0 ; 2.5 -> 2.5, 2.50 ; 12000 -> 12000, 12,000, 12_000."""
    out = set()
    if _is_int(x):
        n = int(round(x))
        out |= {str(n), f"{n}.0"}
        if n >= 1000:
            out |= {f"{n:,}", f"{n:_}"}
    else:
        for d in digits:
            s = f"{x:.{d}f}"
            if abs(float(s) - x) < 1e-9:
                out |= {s, s.rstrip("0")}
    return {s for s in out if s}


def _approx(x):
    """Rounded decimal spellings of a non-round value (hours): 3.333 -> 3.33, 3.3 ; 0.25 -> 0.25, .25."""
    out = set()
    for d in (1, 2):
        s = f"{x:.{d}f}"
        if float(s) > 0 and abs(float(s) - x) / x <= 0.03:
            out |= {s, s.rstrip("0").rstrip(".")}
    out |= {s[1:] for s in out if s.startswith("0.")}
    return {s for s in out if s and s != "0"}


def _alt(strings):
    return "|".join(re.escape(s).replace(r"\ ", r"\s+") for s in sorted(strings, key=len, reverse=True))


_UEND = r"(?![A-Za-z_/]|\.[A-Za-z])"   # a unit ends the word: not 15-minute.json, not 15min_run
_MIN_UNIT = r"(?:\s*[-‑–]?\s*)(?:minutes?|mins?|min)" + _UEND
_SEC_UNIT = r"(?:\s*[-‑–]?\s*)(?:seconds?|secs?|s)" + _UEND
_MS_UNIT = r"(?:\s*[-‑–]?\s*)(?:milliseconds?|ms)" + _UEND
_HR_UNIT = r"(?:\s*[-‑–]?\s*)(?:hours?|hrs?|h)" + _UEND
_NB = r"(?<![\w.$/\\#])"            # numeral start: not glued to a word, decimal point, path or escape
_NE = r"(?![\d]|[.,]\d)"             # numeral end: not followed by more digits


@lru_cache(maxsize=None)
def _patterns(requested_min):
    R = float(requested_min)
    secs = R * 60
    mention_parts = []
    # minutes, numerals and words
    words = set()
    if _is_int(R):
        words |= _words(int(round(R))) if R < 1000 else set()
    frac = {0.25: "and a quarter", 0.5: "and a half", 0.75: "and three quarters"}
    if not _is_int(R) and round(R % 1, 2) in frac and R >= 1:
        for w in _words(int(R)):
            words |= {f"{w} {frac[round(R % 1, 2)]}", f"{w}-{frac[round(R % 1, 2)].replace(' ', '-')}"}
        if int(R) == 1:
            words |= {f"a minute {frac[round(R % 1, 2)]}", f"one minute {frac[round(R % 1, 2)]}"}
    vulgar = {0.25: "¼", 0.5: "½", 0.75: "¾"}
    nums = set(_numerals(R))
    if not _is_int(R) and round(R % 1, 2) in vulgar:
        nums.add(f"{int(R)}{vulgar[round(R % 1, 2)]}")
    minute_core = rf"{_NB}(?:{_alt(nums)}){_NE}"
    if words:
        minute_core = rf"(?:{minute_core}|(?<![\w-])(?:{_alt(words)}))"
    mention_parts.append(minute_core + _MIN_UNIT)
    # "a minute and a quarter" style words already carry the unit
    unit_words = {w for w in words if "minute" in w}
    if unit_words:
        mention_parts.append(rf"(?<![\w-])(?:{_alt(unit_words)})(?![A-Za-z])")
    # seconds and milliseconds
    if _is_int(secs):
        # "720.0 seconds" is how tools print a measured duration: not a restatement
        mention_parts.append(rf"{_NB}(?:{_alt(_numerals(secs) - {f'{int(round(secs))}.0'})}){_NE}{_SEC_UNIT}")
        mention_parts.append(rf"{_NB}(?:{_alt(_numerals(secs * 1000))}){_NE}{_MS_UNIT}")
    # hours
    hours = R / 60
    hour_nums = _numerals(hours) if _is_int(hours) or _is_int(hours * 100) else _approx(hours)
    if hours >= 0.25:
        mention_parts.append(rf"{_NB}(?:{_alt(hour_nums)}){_NE}{_HR_UNIT}")
    named = {15: ["a quarter of an hour", "a quarter-hour", "quarter of an hour", "quarter-hour", "quarter hour"],
             30: ["half an hour", "a half hour", "a half-hour", "half-hour", "half hour"],
             45: ["three quarters of an hour", "three-quarters of an hour"],
             60: ["an hour", "one hour", "a full hour"]}
    if _is_int(R) and int(R) in named:
        mention_parts.append(rf"(?<![\w-])(?:{_alt(named[int(R)])})(?![A-Za-z])")
    # composites: 3 hours 20 minutes, 3h20m, 3h 20min ; 2 minutes 30 seconds, 2m30s
    if R >= 60 and _is_int(R):
        h, m = divmod(int(R), 60)
        if m:
            mention_parts.append(rf"{_NB}{h}{_HR_UNIT}\s*,?\s*(?:and\s+)?{m}{_MIN_UNIT}")
            mention_parts.append(rf"{_NB}{h}h\s*{m}m(?![A-Za-z])")
    if not _is_int(R) and _is_int(secs):
        m, s = divmod(int(round(secs)), 60)
        mention_parts.append(rf"{_NB}{m}{_MIN_UNIT}\s*,?\s*(?:and\s+)?{s}{_SEC_UNIT}")
        mention_parts.append(rf"{_NB}{m}m\s*{s}s(?![A-Za-z])")
    # clock-style durations with an hour field: 0:15:00, 00:15:00, 3:20:00; never a clock time (a date before it, or
    # UTC/Z/AM/PM/an offset after it)
    if _is_int(secs):
        total = int(round(secs))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        mention_parts.append(rf"(?<![\d:T/-])(?<!\d\d-\d\d\s)0?{h}:{m:02d}:{s:02d}(?![\d:.])(?!\s*(?:UTC|GMT|Z\b|[AaPp]\.?[Mm]\b|[+-]\d\d))")
    mention = re.compile("(?i)(?:" + "|".join(mention_parts) + ")")

    # bare numbers equal to the budget, only in budget context (code, key/value dumps)
    bare_vals = set()
    if _is_int(secs):
        bare_vals |= _numerals(secs) - {f"{int(round(secs))}.0"}
    bare_secs = re.compile(rf"{_NB}(?:{_alt(bare_vals)})(?![\w]|[.,]\d)") if bare_vals else None
    bare_min = re.compile(rf"{_NB}(?:{_alt(_numerals(R))})(?![\w]|[.,]\d)")
    return mention, bare_secs, bare_min


# A requested value followed by seconds is a measurement: "12 minutes 5 seconds".
_MEASURED_AFTER = re.compile(r"(?i)^\s*,?\s*(?:and\s+)?\d+(?:\.\d+)?\s*(?:seconds?|secs?|s)(?![A-Za-z])")

# --- budget phrases without the number
_NOT_IN_NAME_L = r"(?<![\w/.\-])"          # never inside an identifier or a path (budget-notes.json stays)
_NOT_IN_NAME_R = r"(?![\w/\-]|\.[A-Za-z])"
_BUDGET_NOUN = (r"(?:(?:work(?:ing)?|task|time|review|test(?:ing)?|effort|verification|reproduction|session)[\s-]+)?"
                r"(?:period|window|session|interval|time[- ]?box|time|duration|budget|minimum|mark|allotment)")
# "your requested test period", "the allotted minutes" (one free word allowed after "requested")
_REQ_NP = (r"(?:user(?:\\*['’])?s?[\s-]+|user[-‑])?(?:requested[\s-]+(?:full\s+)?(?:[\w-]+\s+)?"
           r"(?:period|window|session|interval|duration|time[- ]?box|time|minutes?|budget|minimum|mark|allotment)"
           r"|(?:allotted|allocated|assigned)\s+(?:full\s+)?(?:time|period|window|session|minutes?|duration))")
BUDGET_PHRASE = re.compile(
    r"(?i)" + _NOT_IN_NAME_L + r"(?:" + _REQ_NP +
    # time budget / task budget (not token budgets)
    r"|(?:time|task|work|wall[- ]?clock)[\s-]+budget"
    r"|budgeted\s+time"
    # the work period / the task period / the full work period
    r"|(?:work(?:ing)?|task)[\s-]+period"
    # halfway through my time
    r"|halfway\s+(?:through|into|point\s+of)\s+(?:the\s+|my\s+|your\s+|this\s+)?(?:requested\s+)?"
    r"(?:time|budget|period|window|session)"
    # the rest / remainder of the time
    r"|(?:rest|remainder|balance)\s+of\s+(?:the\s+|my\s+|your\s+|this\s+)?(?:requested\s+)?"
    r"(?:time|budget|period|window|session)"
    # the remaining (review) time
    r"|remaining\s+(?:[\w-]+\s+)?time"
    # the agent's cutoff, set from the deadline (its epoch value is removed as a deadline value)
    r"|(?:planned|fixed|scheduled)\s+cutoff"
    # a repeat run's cutoff set from the deadline, and a reason that points at the removed instruction
    # ("because you asked for careful effort")
    r"|repeat(?:['’]s|\s+run['’]s)\s+cutoff"
    r"|(?:since|because|as)\s+you\s+(?:asked|requested)\s+for\s+(?:thorough|careful|sustained|extended|full)\s+"
    r"(?:work|effort|analysis)"
    # budget windows named without a number ("the remaining test window", "once that window is complete"); not
    # "time window" alone, which also names data windows in task content
    r"|remaining\s+(?:[\w-]+\s+){0,2}(?:window|period|interval)"
    r"|(?:observation|audit|timing|review|work|test|testing|verification|evaluation|effort)[\s-]+window"
    r"|(?:that|this|the)\s+window\s+(?:is|has\s+been)\s+(?:fully\s+)?(?:satisfied|complete|completed|over|up|elapsed|reached)"
    r"|time[- ]window\s+checks?"
    # a stop time set from the budget ("the planned limit")
    r"|planned\s+(?:time\s+)?limit"
    r"|time\s+allocation"
    # talk about the time request itself, and about pacing work against it
    r"|(?:time|timing)\s+request"
    r"|(?:tracked|tracking|watched|watching|checked|monitored)\s+the\s+(?:clock|time)\s+as\s+(?:you\s+)?requested"
    r"|(?:final|last|remaining)\s+(?:part|portion|stretch|phase)\s+of\s+(?:the|my|this)\s+(?:review|session|period|time|window|budget)"
    r"|(?:stay|keep|stayed|staying)\s+on\s+pace"
    r"|pace\s+(?:myself|this|my\s+work|our\s+work|the\s+work|my\s+effort)"
    # "cannot stretch it", "no way to idle": these only explain not filling the requested time
    r"|(?:can['’]?t|cannot|can\s+not)\s+(?:\w+\s+)?stretch\s+(?:this|it|the\s+(?:session|work|time)|my\s+work)"
    r"|no\s+way\s+to\s+(?:\w+\s+)?(?:pause|wait|idle|stall)"
    r"|no\s+way\s+to\s+(?:genuinely|actually|literally|really)\s*$"
    r"|(?:can['’]?t|cannot|can\s+not|way\s+to)\s+idle"
    r"|make\s+(?:the\s+)?(?:wall[- ]?clock\s+)?(?:time|clock)\s+(?:advance|pass)"
    r"|rather\s+than\s+padding"
    r"|pad(?:ding)?\s+(?:the\s+(?:answer|reasoning|response|session|remaining\s+\w+)"
    r"|with\s+(?:\w+\s+){0,2}(?:clock\s+calls|calls|checks|restatements)|to\s+fill|further)"
    # holding a finished answer back is waiting for the deadline ("holding the final result"); not "hold onto X"
    # or "hold for X" (task text)
    r"|hold(?:ing)?\s+(?:back\s+)?(?:the|my|this|our)\s+(?:final\s+|verified\s+|exact\s+)?"
    r"(?:answer|response|reply|submission|dictionary|result)(?!\s+(?:of|from|in|for)\b)"
    # the budget named "a completed interval" once its number is gone
    r"|(?:the|a)\s+completed\s+(?:work\s+)?interval"
    r")" + _NOT_IN_NAME_R)
_REMAIN_NUM = (r"(?:\d+(?:\.\d+)?|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|"
               r"thirty|a\s+few|a\s+couple(?:\s+of)?|few|several)")
REMAINING = re.compile(
    r"(?i)(?:\b(?:about|roughly|approximately|around|just\s+over|just\s+under|over|under|less\s+than|more\s+than|"
    r"nearly|almost|only|~)\s*)?(?<![\w.])" + _REMAIN_NUM + r"(?:\s+and\s+a\s+half)?\s+(?:more\s+)?"
    r"(?:minutes?|mins?|seconds?|secs?)\s+(?:remain(?:s|ing)?|left|to\s+go)\b"
    r"|\b(?:has|have|with)\s+(?:about\s+|roughly\s+|only\s+|just\s+)?" + _REMAIN_NUM +
    r"\s+(?:minutes?|mins?|seconds?|secs?)\s+remaining\b"
    # "the final 3 minutes", "the last 90 seconds"
    r"|\b(?:remaining|last|final)\s+(?:few\s+|couple\s+of\s+|\d+(?:\.\d+)?\s+|one\s+|two\s+|three\s+|four\s+|five\s+)?"
    r"(?:minutes?|mins?|seconds?)\b(?!\s*(?:of|in)\s+(?:the\s+)?(?:log|output|file|trace|data))"
    # "about six minutes away", "two minutes before the deadline"
    r"|(?:\b(?:about|roughly|approximately|only|just|~)\s*)?(?<![\w.])" + _REMAIN_NUM +
    r"\s+(?:more\s+)?(?:minutes?|mins?|seconds?|secs?)\s+(?:away\b|(?:from|before|until)\s+(?:the\s+)?(?:planned\s+|fixed\s+)?"
    r"(?:cutoff|deadline|end\s+of\s+the\s+(?:session|period|window)))")

# --- adverbial phrases (removed on their own)
_PREP = (r"(?:(?:well|long|just|shortly|right|only)\s+)?(?:for|over|within|during|throughout|through|after|across|in|"
         r"before|until|till|by|past|near|approaching|around|at|to|beyond|under|inside|into|as|once|when|per)")
_DET = r"(?:(?:the|a|an|your|my|this|that|its|their)\s+)?"
_REM = (r"(?:(?:remainder|rest|end|balance|close|last\s+(?:few\s+)?minutes|"
        r"remaining\s+(?:\w+\s+){0,2}(?:minutes?|seconds?))\s+of\s+" + _DET + r")?")
_MOD = (r"(?:(?:user(?:\\*['’])?s?|user[-‑]requested|explicitly|originally|requested|full|entire|whole|allotted|allocated|"
        r"assigned|specified|stated|given|planned|minimum|complete|prescribed|short|remaining)[\s-]+)*")
_TAIL = (r"(?:[\s-]+(?:work(?:ing)?|task|time|review|test(?:ing)?|effort|verification|reproduction|reproducibility|"
         r"execution|audit|evaluation|analysis|check(?:ing)?|minimum))?"
         r"(?:[\s-]+(?:period|window|session|interval|run|budget|effort|time[- ]?box|mark|block|slot|limit|target|"
         r"duration|span|allotment|minimum))?"
         r"(?:\s+(?:that\s+)?(?:you|the\s+user|user)\s+(?:requested|asked\s+for|specified|set|gave(?:\s+me)?|allotted)"
         r"|\s+(?:requested|allotted|asked\s+for))?"
         r"(?:\s+(?:ends|elapses|is\s+up|expires|(?:approach|near)(?:es|s|ing)?\s+its\s+end|"
         r"(?:has|have)\s+(?:fully\s+)?(?:elapsed|passed|ended)|"
         r"(?:is|are|has\s+been|have\s+been)\s+(?:fully\s+)?(?:reached|over|done|up|complete|completed|finished|elapsed))"
         r"(?:\s+(?:at|by|around)\s+~?\d{1,2}:\d\d(?::\d\d)?(?:\s*(?:UTC|GMT|Z))?)?)?")
# "8 minutes of my allotted budget are gone" -> "8 minutes are gone" (the elapsed cue stays)
OF_REQUESTED = re.compile(
    r"(?i)(?<=\d\s(?:minutes|seconds))(?<!remaining\s\d\d\sminutes)\s+of\s+(?:the|your|my)\s+(?:user[- ]?)?"
    r"(?:requested|allotted)\s+(?:full\s+)?" + _BUDGET_NOUN + r"\b")


@lru_cache(maxsize=None)
def _adverbials(requested_min):
    mention = _patterns(requested_min)[0].pattern[4:]           # drop the leading (?i)
    num = re.compile(r"(?i)(?:^|(?:\s*,)?\s+)" + _PREP + r"\s+" + _DET + _REM + _DET + _MOD + r"(?P<m>" + mention + r")"
                     + _TAIL)
    word = re.compile(r"(?i)(?:^|(?:\s*,)?\s+)" + _PREP + r"\s+" + _DET + _REM + _DET + _MOD + r"(?P<m>"
                      + _REQ_NP + r"|(?:\w+\s+)?(?:time|task|work)[\s-]+budget|(?:work(?:ing)?|task)[\s-]+period"
                      r"|remaining\s+(?:[\w-]+\s+)?time"
                      r"|remaining\s+(?:[\w-]+\s+){0,2}(?:window|period|interval)"
                      r"|(?:observation|audit|timing|review|work|test|testing|verification|evaluation|effort)[\s-]+window"
                      r"|planned\s+(?:time\s+)?limit)"
                      + _NOT_IN_NAME_R + _TAIL)
    return num, word



# --- deadlines and budget-valued keys
_KEY_TAIL = r"""\\*['"]?\s*[:=,]\s*\\*['"]?"""
DEADLINE_KEY = re.compile(
    r"(?i)(?<![\w])(\w*(?:deadline|minimum_finish|min_finish|minimum_end|min_end|finish_by|end_by|must_end|"
    r"cutoff_epoch|cutoff_utc|cutoff_time|target_end|end_target)\w*)" + _KEY_TAIL +
    r"(\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:?\d\d)?|\d{9,13}(?:\.\d+)?)")
# Identifiers that name the budget, a deadline derived from it, or the time left (requested_secs, run_deadline,
# minimum_end_at, secs_left). Every alternative contains an underscore, so the prose word "deadline" never matches;
# requested_<noun>_* (task content) does not either.
BUDGET_IDENT = re.compile(
    r"(?i)(?<![\w-])(?:\w*requested_(?:\w+_)?(?:minutes?|seconds?|secs?|duration|period|time|end|window)(?:_\w+)?"
    r"|\w*full_requested_\w+|\w+_deadline\w*|deadline_\w+|\w*minimum_(?:finish|end)\w*|\w*min_finish\w*"
    r"|completed_work_period|\w*cutoff_(?:epoch|utc|time)\w*"
    r"|\w*(?:audit|observation|timing)_window\w*|\w*remaining_(?:seconds|secs|minutes|time)\w*"
    r"|\w*(?:seconds|secs|minutes|time)_(?:left|remaining)\w*|\w*planned_time_limit\w*)(?![\w-])")
# Timestamps: ISO / "YYYY-MM-DD HH:MM:SS[ UTC]" and epoch seconds or milliseconds
_TS_ISO = r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d|\s?UTC)?"
_TS_EPOCH = r"1[5-9]\d{8}(?:\d{3})?(?:\.\d+)?"
_KEYED_TS = re.compile(r"(?<![\w])(?P<key>[A-Za-z_]\w*)" + _KEY_TAIL +
                       r"(?P<val>" + _TS_ISO + r"|" + _TS_EPOCH + r")(?![\d])")
# a deadline instant that is also a clock-tool or tool-recorded reading ({"utc": ...}, "updatedAt": ...) is a
# measurement there and keeps its value
_TOOL_TIME_KEY = re.compile(r"(?:(?i:current_?time|\bnow|timestamp|\butc|\bdate|\btime)|[a-z]At)\\*[\"']?\s*[:=]\s*\\*[\"']?$")
_END_KEY = re.compile(r"(?i)end|stop|finish|until|due|expir|target|minimum|limit|cutoff|deadline")
_START_KEY = re.compile(r"(?i)start|begin|began")
_BUDGET_KEY = re.compile(r"(?i)(?:requested|budget|allotted|target|deadline|minimum|work_period|period|window)\w*"
                         + _KEY_TAIL + r"$")
# words that make a bare number a budget number (same line, within 60 chars before or 40 after)
_BUDGET_CONTEXT = re.compile(r"(?i)requested|budget|deadline|allotted|remaining|elapsed|minimum|work[_ ]?period|"
                             r"target[_ ](?:seconds|secs|minutes|duration)|window|until|full")

# --- segmentation
_HARD = re.compile(
    r"(?:\\+[nrt]|\r?\n)(?:[+\-](?![\d.]))?"            # real or escaped newline, plus a diff marker after it
    r"|\t|\\*\"|`|[{}\[\]<>|]"
    r"|(?<=[\s(=,\[{:][fFbBrRuU])\\*'"                  # f'...' / b'...' string prefixes
    r"|(?<![A-Za-z0-9\\])\\*'|\\*'(?![A-Za-z0-9])"         # a quote that is not an apostrophe (user\'s is one)
    r"|(?:(?<=\s)|^)(?:#|//)(?=\s)")                     # comment starts
_SENT_END = re.compile(r"[.!?…](?=\s|$)")
_CLAUSE_SEP = re.compile(r",\s+|;\s*|:\s+|\s+[—–]\s+|—|\s+--\s+|\s+-\s+|\s*\(|\)")
_CODEY = re.compile(r"[={}$]|&&|\|\||\+\+|\w\(|;\s*$|\bsleep\s+\d")
_CONTINUATION = re.compile(r"(?i)^(?:ending|ends|which\s+ends|that\s+ends|expiring|expires|due|finishing)\s+"
                           r"(?:at|on|by|around)\b")
# After a deleted first clause, the rest stands as its own sentence only if it starts like one.
_INDEPENDENT = re.compile(r"(?i)^(?:I\b|I['’](?:ll|m|ve|d)\b|we\b|it\b|it['’]s\b|this\b|these\b|that\b|the\b|then\b|"
                          r"so\b|but\b|and\s+(?:I|the|it|then|this)\b|[A-Z]\w*\s+(?:is|are|was|were|has|have)\b)")


def _mentions(seg, R):
    """Spans (start, end, class) of budget mentions in a segment, measurement exemptions removed."""
    mention = _patterns(R)[0]
    spans = []
    for m in mention.finditer(seg):
        if _MEASURED_AFTER.match(seg[m.end():]):
            continue
        spans.append((m.start(), m.end(), "value"))
    spans += [(m.start(), m.end(), "phrase") for m in BUDGET_PHRASE.finditer(seg)]
    spans += [(m.start(), m.end(), "remaining") for m in REMAINING.finditer(seg)]
    return sorted(spans)


def _cap(text):
    i = len(text) - len(text.lstrip())
    if text[i:i + 1].islower() and not text[i + 1:i + 2].isupper():
        return text[:i] + text[i].upper() + text[i + 1:]
    return text


def _join(left, right):
    """Glue two pieces after a deletion, capitalizing the right one when it now starts a sentence."""
    if not left.strip() or left.rstrip()[-1:] in (".", "!", "?"):
        right = _cap(right)
    return left + right


def _sentences(seg):
    """[(start, end)] of sentences: start after leading whitespace, end including the terminator."""
    out, pos = [], 0
    for m in list(_SENT_END.finditer(seg)) + [None]:
        end = m.end() if m else len(seg)
        start = pos + (len(seg[pos:end]) - len(seg[pos:end].lstrip()))
        if start < end:
            out.append((start, end))
        pos = end
    return out


def _clause_bounds(body, a, b):
    """Expand [a, b) to its clause: (clause_start, clause_end, left_sep, right_sep)."""
    left_sep = right_sep = None
    cs, ce = 0, len(body)
    for m in _CLAUSE_SEP.finditer(body):
        if m.end() <= a:
            cs, left_sep = m.end(), m
        elif m.start() >= b:
            ce, right_sep = m.start(), m
            break
    return cs, ce, left_sep, right_sep


# Seams. An adverbial that is the whole predicate ("the logs are within the requested period.") is not removed
# on its own: the clause goes.
_PREDICATE_END = re.compile(r"(?i)(?:\bI['’]m|\bI\s+am|\b(?:is|are|was|were|be|been|being|remain|remains|remained|"
                            r"stay|stays|stayed|still))\s*$")
# holding verbs whose only object was the budget ("stay active [until the 12 minutes pass], then reply")
_HOLD = r"keep\s+(?:the\s+|this\s+)?session\s+open|keep\s+it\s+open|hold\s+off|stay\s+active"
_BARE_WORK_END = re.compile(r"(?i)(?:\b(?:work|working|wait|waiting|continue|continuing)|\b(?:" + _HOLD + r"))\s*$")
_WORK_VERB = re.compile(r"(?i)(?:^|(?<=[.;:!?]\s)|(?<=,\s)|(?<=\band\s)|(?<=\bthen\s)|(?<=\bI['’]ll\s)|(?<=\bI\swill\s)"
                        r"|(?<=\bwe['’]ll\s))(?:keep\s+working|continue\s+working|work|" + _HOLD + r")\s*$")
_CONNECTIVE = re.compile(r"(?i)^\s*,?\s*(to|before|and|then)\s+(\w+)")
_GERUND = {"submitting": "submit", "returning": "return", "reporting": "report", "delivering": "deliver",
           "answering": "answer", "finishing": "finish", "giving": "give", "providing": "provide", "saving": "save",
           "writing": "write", "finalizing": "finalize", "completing": "complete", "handing": "hand",
           "producing": "produce", "sending": "send", "responding": "respond", "stopping": "stop",
           "recording": "record", "testing": "test", "checking": "check", "verifying": "verify"}
# Using the budget becomes plain doing ("We’re using the remaining minutes to diff X" -> "We’ll diff X"; "spend
# the rest of the period on Y" -> "do Y"; "is given the remaining time to measure" -> "is run to measure"); no
# "take/spend some time", which still reads as filling a period.
_REST_OF = (r"(?:the|my)\s+(?:remaining\s+(?:[\w-]+\s+){0,3}?(?:time|window|period|minutes)"
            r"|rest\s+of\s+(?:the|my)\s+(?:[\w-]+\s+){0,2}?(?:time|window|period)"
            # "use the requested 12 minutes to diff X" -> "diff X"
            r"|(?:full\s+|entire\s+)?(?:user[- ])?(?:requested|allotted)\s+(?:full\s+)?(?:[\w.-]+\s+){0,3}?"
            r"(?:time|window|period|minutes?|hours?))")
_REWRITES = (
    (re.compile(r"\b(I|We)(['’])(?:m|re)\s+(?:now\s+)?using\s+" + _REST_OF + r"\s+to\s+"), r"\1\2ll "),
    (re.compile(r"\b(I|We)(['’])(?:m|re)\s+(?:now\s+)?using\s+" + _REST_OF + r"\s+(?:on|for)\s+"), r"\1\2ll do "),
    (re.compile(r"(?i)\b(?:use|spend|devote)\s+" + _REST_OF + r"\s+to\s+"), r""),
    # "devote the rest of the period for a partial retry" -> "do a retry"
    (re.compile(r"(?i)\b(?:use|spend|devote)\s+" + _REST_OF + r"\s+(?:on|for)\s+(an?\s+)partial\s+"), r"do \1"),
    (re.compile(r"(?i)\b(?:use|spend|devote)\s+" + _REST_OF + r"\s+(?:on|for)\s+"), r"do "),
    # "spend the remaining minutes and <verb>" is not rewritten (it announces a deadline stop): the clause step
    # deletes it.
    (re.compile(r"(?i)\b(is|are|was|were)\s+(?:allotted|allocated|given)\s+the\s+remaining\s+(?:\w+\s+)?time\s+to\b"),
     r"\1 run to"),
    # "fine, but as you asked for extended effort, I will" -> "fine, but I will"
    (re.compile(r"(?i)(?<=\s)(?:since|because|as)\s+you\s+(?:asked|requested)\s+for\s+(?:thorough|careful|sustained|"
                r"extended|full)\s+(?:work|effort|analysis),\s+"), r""),
)
# A purpose sub-clause that carries the mention goes on its own: "Run the A check so that we can fill the 12
# minutes, then B" -> "Run the A check, then B"
_FINISH = r"\b(?:finish|finishes|finished|finishing|complete|completes|completing|completion|done|end|ends|ending)\b"
_PURPOSE = re.compile(r"(?i)\s+(?:so\s+(?:that\s+)?(?:I|we|it|you)\s+(?:can|could|will|would|may)|in\s+order\s+to"
                      # "so the suite can finish within the budget" (only about finishing: "so the log spans
                      # the budget" is the budget itself and its sentence goes whole)
                      r"|so\s+(?:that\s+)?(?:the|this|these|everything|all|my|our)(?=\s[^,;:.!?]*" + _FINISH + r"))\s+")
# a parenthesis of clock readings right after a removed clause goes with it ("..., the 12 minutes are up
# (10:00 -> 10:12 UTC), and ..."): it dated the budget span; the clock-tool results stay
_CLOCK_PAREN = re.compile(r"\s*\(\s*~?\d{1,2}:\d\d(?::\d\d)?(?:\s*(?:UTC|Z))?\s*(?:→|->|–|—|-|to)\s*~?\d{1,2}:\d\d(?::\d\d)?"
                          r"(?:\s*(?:UTC|Z))?\s*\)")


# "rather than pad the response", "instead of padding with clock calls" (removed in place)
PAD_ADV = re.compile(r"(?i)(?:^|(?:\s*,)?\s+)(?:rather\s+than|instead\s+of)\s+pad(?:ding)?(?P<m>"
                     r"(?:\s+(?:the\s+(?:answer|reasoning|response|remaining\s+\w+)(?:\s+with\s+(?:\w+\s+){0,2}clock\s+calls)?"
                     r"|with\s+(?:\w+\s+){0,2}(?:clock\s+calls|calls)(?:\s+or\s+restatements)?"
                     r"|to\s+fill\s+(?:[\w-]+\s+){0,3}?(?:minutes?|time|window)))?)(?=[\s,.;:—–)]|$)")


# the subject of a deleted first clause, carried to the clause after it, and clause starts that are not
# a verb phrase (those are left to _fix_orphan_start)
_SUBJ_LEAD = re.compile(r"^((?:I|We)(?:['’]ll|\s+will|['’]m\s+going\s+to|\s+need\s+to)|Let\s+me|let\s+me)\s+(?:now\s+)?[a-z]")
# the carried subject needs a verb after it ("I’ll save X", never "I’ll structures")
_CARRY_VERB = re.compile(r"(?:save|return|record|run|rerun|re-run|check|verify|write|submit|report|compute|recompute|"
                         r"finish|produce|confirm|deliver|provide|update|create|inspect|compare|validate|test|document|"
                         r"clean|keep|give|send|compile|render|build|generate|copy|store|print|review|summarize|"
                         r"finalize|answer|output|rename|move|archive|close|complete|state|list|package|attach|hand|"
                         r"do|make|add|note|log|capture|audit|examine|look|read|trace|stop|end)\b")
_DEPENDENT_LEAD = re.compile(r"(?i)\s*(?:including|along\s+with|together\s+with|as\s+well\s+as|plus)\b")


_AND_TO = re.compile(r"(\b(?:I|We)(?:['’]ll|\s+(?:am|are)\s+going\s+to)\s(?:(?![.!?](?:\s|$))[^;\n])*?,\s+and)\s+to\s+"
                     r"(?=[a-z])")


def _fix_oxford(s):
    """'save X, and return Y' -> 'save X and return Y' (two items left of a three-item list)."""
    return re.sub(r"^([^,;:]*?),\s+(and|or)\s+", r"\1 \2 ", s, count=1)


_HONESTLY = re.compile(r"(?i)^([^.:;!?]*?\b(?:close\s+out|stop|stopping|finish|wrap\s+up|end\s+here|conclude))\s+honestly\b")


def _fix_work_verb(left, right):
    """'Work [for the full 12 minutes] before sending X' -> 'Send X'; '... work [...] and return X' -> 'return X';
    'Work [...] to test X' -> 'Test X'. Returns (left, right), unchanged when the pattern does not apply."""
    mv, mc = _WORK_VERB.search(left), _CONNECTIVE.match(right)
    if not mv or not mc:
        return left, right
    conn, word = mc.group(1).lower(), mc.group(2)
    head = left[:mv.start()]
    rest = right[mc.end(1):].lstrip()
    if conn == "before":
        base = _GERUND.get(word.lower())
        rest = (base + rest[len(word):]) if base else rest
    at_start = not head.strip() or head.rstrip()[-1:] in ".!?:"
    if at_start:
        rest = _cap(rest)
    return head, rest


def _sub_cap(pat, repl, seg):
    """pat.subn(repl, seg), capitalizing a replacement that starts a sentence ("Spend the rest of the period on the
    tests." -> "Do the tests.")."""
    out, pos, n = [], 0, 0
    for m in pat.finditer(seg):
        head = seg[pos:m.start()]
        rep = m.expand(repl)
        done = "".join(out) + head
        at_start = not done.strip() or done.rstrip()[-1:] in (".", "!", "?")
        out.append(head)
        if at_start and rep:
            rep = rep[0].upper() + rep[1:]
        out.append(rep)
        pos = m.end()
        n += 1
    if not n:
        return seg, 0
    out.append(seg[pos:])
    return "".join(out), n


# a sentence that only says how the budget will be spent goes whole ("We will use the requested 12 minutes for
# linting.") instead of leaving a stub ("We will do linting.")
_USE_FOR_SENTENCE = re.compile(
    r"(?:^|(?<=[.!?]\s))(?:I|We)(?:['’]ll|\s+will)\s+(?:also\s+)?(?:use|spend|devote)\s+(?P<np>(?:the|my)\s+"
    r"(?:[\w.’'-]+\s+){0,5}?(?:time|window|period|minutes?|hours?)(?:\s+(?:you|the\s+user)\s+(?:requested|asked\s+for))?)"
    r"\s+(?:on|for)\s+[^.!?\n]*[.!?](?=\s|$)")
# an adverbial that the clause step removes with its clause instead of on its own: after a degree adverb ("fits
# comfortably [within the budget]"), in a purpose clause about finishing ("so the suite can finish [within the
# budget]"), or as a reason (", per the requested period").
_DEGREE_END = re.compile(r"(?i)\b(?:comfortably|safely|easily|well|squarely|neatly)\s*$")
_PURPOSE_SO = re.compile(r"(?i)\bso\s+(?:that\s+)?(?:the|this|these|everything|all|my|our)\b[^,;:.!?—–()]*" + _FINISH
                         + r"[^,;:.!?—–()]*$")
_KEEP_WORKING = re.compile(r"(?i)\b(?:keep|continue)\s+working\b(?=[^,;:.!?]*$)")


def _drop_use_for(seg):
    out, pos = [], 0
    for m in _USE_FOR_SENTENCE.finditer(seg):
        out.append(seg[pos:m.start()])
        pos = m.end()
    if not out:
        return seg
    out.append(seg[pos:])
    res = "".join(out)
    return res.rstrip(" ") if pos >= len(seg) else res


def _remove_adverbials(seg, R, flags):
    num_adv, word_adv = _adverbials(R)
    seg = _drop_use_for(seg)
    seg = OF_REQUESTED.sub("", seg)
    for i, (pat, repl) in enumerate(_REWRITES):
        seg, n = _sub_cap(pat, repl, seg)
        if n and i < 2:
            flags["_using_rewrite"] = 1
    for pat in (num_adv, word_adv, PAD_ADV):
        pos = 0
        while True:
            m = pat.search(seg, pos)
            if not m:
                break
            if _MEASURED_AFTER.match(seg[m.end("m"):]):
                pos = m.end()                                  # "in 12 minutes 5 seconds" is a measurement
                continue
            left, right = seg[:m.start()], seg[m.end():]
            clause_left = re.split(r"[,;:—–()]|\s-\s", left)[-1]
            if pat is not PAD_ADV and (
                    _DEGREE_END.search(left) or _PURPOSE_SO.search(clause_left)
                    or re.match(r"(?i)\s*,?\s*(?:as\s+)?per\s", m.group(0))):
                pos = m.end()                                  # the clause step removes the clause
                continue
            if pat is PAD_ADV:
                # "..., I will stop honestly: ..." -> "I will stop: ..." (the honesty was about not filling
                # the time)
                right = _HONESTLY.sub(r"\1", right, count=1)
            if (_PREDICATE_END.search(left) or _BARE_WORK_END.search(left)) and (
                    not right.strip() or right.lstrip()[:1] in ".,;:!?)—–" or re.match(r"(?i)\s+and\b", right)):
                if _fix_work_verb(left, right) == (left, right):
                    pos = m.end()                              # the clause step removes the whole predicate
                    continue
            if left.rstrip()[-1:] in (".", "!", "?") and right.lstrip()[:1] == ",":
                right = _cap(right.lstrip()[1:].lstrip())      # "ready. [Once ... elapsed], it will" -> "ready. It will"
                left = left if left.endswith(" ") else left + " "
            if not left.strip() and right.lstrip()[:1] == ",":
                right = right.lstrip()[1:].lstrip()
            left, right = _fix_work_verb(left, right)
            # "keep working on the docs [for the requested 12 minutes]" -> "work on the docs"
            mk = _KEEP_WORKING.search(left)
            if mk:
                left = left[:mk.start()] + ("W" if mk.group(0)[:1].isupper() else "w") + "ork" + left[mk.end():]
            seg = _join(left, right)
            pos = max(0, len(left) - 1)
    return seg


# --- what a cut leaves behind
_EMPTIED, _CONT = "", ""          # private-use marks, never in session text (checked before use)
# a sentence cut at a line end runs on when it ends on a function word ("... is saved in\n`out/`.")
_OPEN_END = re.compile(r"(?i)\b(?:in|at|to|the|a|an|and|or|of|for|by|with|as|on|from|into|is|are|was|were|be|that|"
                       r"which|until|before|after|than)\s*$")
# a clause or sentence left starting with a connective that pointed at the removed text
_ORPHAN_LEAD = re.compile(r"(?i)^(\s*)(?:then|but|and|yet|so(?!\s+(?:far|that|much|many)\b))\s+(?=\S)")
# a next sentence whose subject is the removed one ("<removed sentence>. These will be kept too.")
_ANAPHOR = re.compile(r"^(?:(?:These|Those|They)\s+(?:will|are|were|have|should|can|would)|(?:This|That|It)\s+"
                      r"(?:will|should|would)|(?:They|It)['’]ll)\b")


def _fix_orphan_start(rest):
    """After the first clause went: 'then will submit X' -> 'I will submit X'; 'but I’ll go on past it' -> 'I’ll go
    on'; 'then I’ll record X' -> 'I’ll record X'."""
    m = _ORPHAN_LEAD.match(rest)
    if not m:
        return rest
    after = rest[m.end():]
    if re.match(r"(?i)(?:will|would|can|could|should|shall|must|need\s+to|am)\b", after):
        after = "I " + after
    after = re.sub(r"(?i)\s+(?:past|beyond)\s+(?:it|that)(?=[\s,.;:!?]|$)", "", after, count=1)
    return m.group(1) + after


def _fix_after_deleted(tail):
    """The sentence after a deleted one that only continues it through a pronoun ('These will be saved ...') goes."""
    lead = tail[:len(tail) - len(tail.lstrip(" "))]
    t = tail[len(lead):]
    if _ANAPHOR.match(t):
        sents = _sentences(t)
        if sents and sents[0][0] == 0:
            e0 = sents[0][1]
            if t[e0 - 1:e0] in (".", "!", "?"):
                return lead + t[e0:].lstrip(" ")
    return tail


_QL = r"(?P<q>(?:\\*\"){3}|(?:\\*'){3}|\\*\"|\\*')" + _EMPTIED + r"(?P=q)"      # an emptied string literal
_ONLY_LITERAL = re.compile(r"\s*(?:" + _QL + r"|" + _EMPTIED + r")\s*[;,]?\s*")
_ONLY_PRINT = re.compile(r"\s*(?:print|console\.log|echo|printf|puts|text|log)\s*\(?\s*" + _QL + r"\s*\)?\s*;?\s*")
_EMPTY_CELL = re.compile(r"\|\s*" + _EMPTIED + r"\s*\|")
_INLINE_EMPTIED = (
    (re.compile(r"(?<=\()\s*" + _QL + r"\s*,\s*"), ""),                                  # print('', x) -> print(x)
    (re.compile(r"(?P<pre>[{(,]\s*)[A-Za-z_]\w*\s*[:=]\s*" + _QL + r"\s*,\s*"), r"\g<pre>"),   # {explanation:"",plan
)
_NEWLINE_AT = re.compile(r"(\\+n|\r?\n)([+-](?![\d.]))?")


def _blank_line(line):
    return not _DIFF_MARK.sub("", line, count=1).strip()


def _resolve_cont(text):
    """A sentence deleted at a line end that ran on to the next line: that line goes too when it ends the sentence."""
    out, pos = [], 0
    while True:
        i = text.find(_CONT, pos)
        if i < 0:
            break
        out.append(text[pos:i].rstrip(" "))
        pos = i + 1
        m = _NEWLINE_AT.match(text, i + 1)
        if not m:
            continue
        rest = text[m.end():]
        ends = [x for x in (rest.find("\n"), rest.find("\\n")) if x >= 0]
        le = min(ends) if ends else len(rest)
        if re.search(r"[.!?](?=\s|$)", rest[:le]) is None:
            continue
        pos = m.end() + le
    out.append(text[pos:])
    return "".join(out)


def _resolve_emptied(text):
    """Structure an emptied segment leaves: a string whose first line was emptied loses that line; print('', x) ->
    print(x); an item whose value was emptied goes ({note:"", items} -> {items}); a line that is only an emptied
    literal, only print(''), a table row with an emptied cell, or nothing at all goes."""
    text = re.sub(r"(?<=[\"'])" + _EMPTIED + r"(?:\\+n|\r?\n)", "", text)
    for pat, repl in _INLINE_EMPTIED:
        text = pat.sub(repl, text)
    if _EMPTIED not in text:
        return text
    pieces = _LINE_SPLIT.split(text)
    lines, seps = pieces[0::2], pieces[1::2] + [""]
    drop = [False] * len(lines)
    for i, line in enumerate(lines):
        if _EMPTIED not in line or drop[i]:
            continue
        body = _DIFF_MARK.sub("", line, count=1)
        if (_ONLY_LITERAL.fullmatch(body) or _ONLY_PRINT.fullmatch(body)
                or (body.lstrip().startswith("|") and _EMPTY_CELL.search(body))):
            drop[i] = True
            prev = next((lines[j] for j in range(i - 1, -1, -1) if not drop[j]), None)
            if (prev is None or _blank_line(prev) or prev.startswith("*** ")) and i + 1 < len(lines) \
                    and _blank_line(lines[i + 1]):
                drop[i + 1] = True                     # no double (or leading) blank line where a paragraph went
    out = []
    for i, (l, s) in enumerate(zip(lines, seps)):
        if drop[i]:
            if not s and out:                          # the last line: the separator before it goes instead
                out[-1] = (out[-1][0], "")
            continue
        out.append((l, s))
    return "".join(l + s for l, s in out)


def _resolve_sentinels(text):
    if _CONT in text:
        text = _resolve_cont(text)
    if _EMPTIED in text:
        text = _resolve_emptied(text)
    return text.replace(_EMPTIED, "").replace(_CONT, "")


def _strip_segment(seg, R, flags):
    """Steps 4-5 on one hard-bounded segment."""
    seg = _remove_adverbials(seg, R, flags)
    for _ in range(200):
        spans = _mentions(seg, R)
        if not spans:
            break
        a, b, _ = spans[0]
        ss, se = next(((s, e) for s, e in _sentences(seg) if s <= a < e), (0, len(seg)))
        sent = seg[ss:se]
        term = sent[-1] if sent[-1:] in ".!?…" else ""
        body = sent[:len(sent) - len(term)]
        ra, rb = a - ss, min(b - ss, len(body))
        cs, ce, lsep, rsep = _clause_bounds(body, ra, rb)
        # a deadline continuation after the budget clause ("..., ending at 10:12Z") goes with it
        while rsep is not None and rsep.group(0).startswith(",") and _CONTINUATION.match(body[rsep.end():]):
            _, ce, _, rsep = _clause_bounds(body, rsep.end(), rsep.end() + 1)
        # a clause that is only a budget reason (", per the requested period") takes the clause it gives the
        # reason for with it
        if lsep is not None and lsep.group(0).startswith(",") and re.match(r"(?i)(?:as\s+)?per\s", body[cs:ce]):
            cs, _, lsep, _ = _clause_bounds(body, lsep.start(), lsep.start())
        pm = None
        for pm in _PURPOSE.finditer(body, cs, ra):
            pass
        pm_start = pm.start() if pm is not None else None
        if pm_start is not None and len(body[cs:pm_start].split()) >= 3:
            new_body = _join(body[:pm_start], body[ce:])
            seg = seg[:ss] + new_body + term + seg[se:]
            continue
        rest_after = body[rsep.end():] if rsep is not None else ""
        # a first clause that only held the budget ("I’ll fill the 12 minutes, save X, and return Y") goes and its
        # subject carries over: "I’ll save X and return Y"
        if lsep is None and cs == 0 and rsep is not None and rsep.group(0).startswith(","):
            subj = _SUBJ_LEAD.match(body[cs:ce])
            ra = rest_after.lstrip()
            if subj and _CARRY_VERB.match(ra):
                seg = seg[:ss] + subj.group(1) + " " + _fix_oxford(ra) + term + seg[se:]
                continue
        whole = (body[:cs].strip(" ,;:—–-(") == "" and body[ce:].strip(" ,;:—–-()") == "") or (
            lsep is None and rsep is not None and rsep.group(0).startswith(",")
            and len(rest_after) < 80 and not _INDEPENDENT.match(rest_after)) or (
            # "Done with the requested period, including X and Y." leaves no main clause
            lsep is None and rsep is not None and rsep.group(0).startswith(",") and _DEPENDENT_LEAD.match(rest_after))
        if whole:
            # the whole sentence goes; keep the terminator when the segment began mid-sentence (", and ... .")
            keep_term = term if body[:1] in (",", ";", ":") and not seg[:ss].strip() else ""
            head, tail = seg[:ss], seg[se:]
            if not keep_term:
                tail = _fix_after_deleted(tail)
            if se == len(seg) and not term and _OPEN_END.search(body):
                flags["_cont"] = 1                          # the sentence runs on past the line end
            if not head.strip():
                tail = tail.lstrip(" ") if not keep_term else tail
            elif not tail.strip() or tail[:1] == " ":
                head = head.rstrip(" ") if not tail.strip() else head
                tail = tail.lstrip(" ") if tail[:1] == " " else tail
            seg = head + keep_term + tail
            continue
        if lsep is not None and lsep.group(0).strip() == "(" and rsep is not None and rsep.group(0) == ")":
            cut_a, cut_b = lsep.start(), rsep.end()          # the parenthesis goes with its only clause
        elif (lsep is not None and lsep.group(0).strip() == ":" and rsep is not None
              and rsep.group(0).strip() in (";", ",")):
            cut_a, cut_b = lsep.end(), rsep.end()            # "A note: [clause]; rest" -> "A note: rest"
        elif lsep is None:
            cut_a, cut_b = cs, (rsep.end() if rsep is not None and rsep.group(0) != ")" else ce)
        else:
            cut_a, cut_b = lsep.start(), ce
        if rsep is not None and rsep.group(0).strip() == "(":
            mcp = _CLOCK_PAREN.match(body, rsep.start())
            if mcp:
                cut_b = mcp.end()
        rest = body[cut_b:]
        # a middle list item went ("log A, the requested 12 minutes, and B" -> "log A and B"): with two items left,
        # no comma before "and"
        if (lsep is not None and lsep.group(0).startswith(",") and rsep is not None and rsep.group(0).startswith(",")
                and cut_a == lsep.start() and re.match(r",\s+(?:and|or)\s", rest) and "," not in body[:cut_a]):
            rest = rest[1:]
        if lsep is None and cut_a == 0:
            rest = _fix_orphan_start(rest)
        new_body = _join(body[:cut_a], rest)
        seg = seg[:ss] + new_body + term + seg[se:]
    return seg



def _replace_tokens(text, R, extra_values=()):
    """Steps 2-3: deadline values and bare budget numbers -> [removed]."""
    def deadline(m):
        return m.group(0)[:m.start(2) - m.start(0)] + REMOVED
    text = DEADLINE_KEY.sub(deadline, text)
    def dv(m):
        before = re.sub(r"\d{4}-\d\d-\d\d[T ]?$", "", text[max(0, m.start() - 60):m.start()])
        if _TOOL_TIME_KEY.search(before):
            return m.group(0)                                 # a clock or tool reading: a measurement, kept
        ls, le, _ = _line_bounds(text, m.start())
        if text[ls:le].rstrip("\\").strip() == m.group(0) and re.fullmatch(_TS_ISO, m.group(0)):
            return m.group(0)                                 # a bare ISO time on its own line is a
                                                              # `date -u` reading
        return REMOVED
    for v in extra_values:
        if v in text:
            # also right after an escaped newline or tab ("...\\n2026-01-02T03:04:05Z\\n")
            text = re.sub(rf"(?:(?<![\w.])|(?<=\\[nt])){re.escape(v)}(?![\w]|\.\d)", dv, text)
    _, bare_secs, bare_min = _patterns(R)
    out, pos = [], 0
    cands = []
    if bare_secs is not None:
        cands += [(m, "s") for m in bare_secs.finditer(text)]
    cands += [(m, "m") for m in bare_min.finditer(text)]
    cands.sort(key=lambda t: t[0].start())
    last_end = -1
    for m, unit in cands:
        if m.start() < last_end:
            continue
        line_start = max(text.rfind("\n", 0, m.start()), text.rfind("\\n", 0, m.start()) + 1, 0)
        before = text[max(line_start, m.start() - 60):m.start()]
        after = text[m.end():m.end() + 40].split("\n")[0].split("\\n")[0]
        # a following unit makes it a prose mention (handled by the phrase/clause steps), not a bare number
        if re.match(r"\s*[-‑–]?\s*(?:minutes?|mins?|seconds?|secs?|s\b|ms\b|hours?|h\b)", after, re.I):
            continue
        keyed = _BUDGET_KEY.search(before)
        if unit == "m":
            ok = bool(keyed)                                   # a bare minute value only as a keyed value
        else:
            ctx = before[-60:] + " " + after
            ok = bool(keyed) or bool(_BUDGET_CONTEXT.search(ctx))
        if not ok:
            continue
        out.append(text[pos:m.start()])
        out.append(REMOVED)
        pos = last_end = m.end()
    out.append(text[pos:])
    return "".join(out)


# --- code structure around the budget
_LINE_SPLIT = re.compile(r"(\\+n|\r?\n)")
_DIFF_MARK = re.compile(r"^\s*[+-]{1,3}\s?")
_CODE_START = re.compile(r"(?i)^\s*(?:assert|if|elif|while|until|for|store|print|printf|echo|timeout|sleep|const|let|"
                         r"var|return|raise)\b")
_XTRACE_ASSIGN = re.compile(r"^\s*\++\s*(\w+)=(\d+(?:\.\d+)?)\s*$")
_ITEM_KEY = re.compile(r"(?i)(?P<q>\\*[\"']?)(?P<key>" + BUDGET_IDENT.pattern[4:] + r")(?P=q)\s*[:=](?!=)\s*")
# any key whose value is only a removed deadline ("updatedAt":[removed] in a goal tool's output): the item goes
_ITEM_REMOVED = re.compile(r"(?P<q>\\*[\"']?)(?P<key>[A-Za-z_]\w*)(?P=q)\s*[:=](?!=)\s*"
                           r"(?=\\*[\"']?\[removed\]\\*[\"']?\s*(?:[,}\])]|\\+n|\n|$))")


# an item whose value is a budget identifier (state:"past_deadline") goes the same way
_ITEM_IDENT_VALUE = re.compile(r"(?P<q>\\*[\"']?)(?P<key>[A-Za-z_]\w*)(?P=q)\s*[:=](?!=)\s*(?=\\*[\"']"
                               + BUDGET_IDENT.pattern[4:] + r"\\*[\"'])")


def _is_code_line(line):
    body = _DIFF_MARK.sub("", line, count=1)
    return bool(_CODEY.search(body) or _CODE_START.match(body))


def _taint_xtrace(text):
    """Shell xtrace echoes a value computed from a removed deadline ('++ python3 -c ...[removed]...' then
    '+ secs=533'): that value, and its other copies in the same string ('timeout 533s ...'), go."""
    if REMOVED not in text:
        return text
    pieces = _LINE_SPLIT.split(text)
    vals = set()
    for i in range(2, len(pieces), 2):
        m = _XTRACE_ASSIGN.match(pieces[i])
        if m and REMOVED in pieces[i - 2] and len(m.group(2)) >= 2:
            vals.add(m.group(2))
    for v in sorted(vals, key=len, reverse=True):
        text = re.sub(rf"(?<![\w.]){re.escape(v)}(?![\d.])", REMOVED, text)
    return text


def _value_end(text, i):
    """End index of a literal or simple expression starting at i (stops at a top-level comma, closing bracket or line
    end)."""
    mq = re.match(r"(\\*)([\"'])", text[i:])
    if mq:
        close = text.find(mq.group(0), i + len(mq.group(0)))
        return None if close < 0 else close + len(mq.group(0))
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif c == "," and depth == 0:
            return j
        elif c == "\n" or (c == "\\" and depth == 0 and re.match(r"\\+n", text[j:])):
            return j
        elif c in "\"'" and depth > 0:
            close = text.find(c, j + 1)
            j = close if close > 0 else j
        j += 1
    return j


def _line_bounds(text, a):
    """(start, end, sep_start) of the line holding index a; sep_start is where the separator before it begins."""
    p1, p2 = text.rfind("\n", 0, a), text.rfind("\\n", 0, a)
    start = max(p1 + 1, p2 + 2 if p2 >= 0 else 0)
    sep_start = start - 1 if p1 + 1 == start and p1 >= 0 else start - 2
    while sep_start > 0 and text[sep_start - 1] == "\\" and start - 1 != p1:
        sep_start -= 1
    ends = [x for x in (text.find("\n", a), text.find("\\n", a)) if x >= 0]
    end = min(ends) if ends else len(text)
    # a newline escaped more than once ('\\\\n' in a JSON string inside a JSON string) starts at its
    # first backslash, so the line does not end in stray backslashes (a blank line then reads as blank)
    while end > start and end < len(text) and text[end] == "\\" and text[end - 1] == "\\":
        end -= 1
    return start, end, max(sep_start, 0)


def _drop_budget_items(text):
    """Key/value items whose key names the budget, a deadline or the time left ('requested_secs': 720,
    "run_deadline": "...", secs_left: max(0, ...)) are deleted with one adjacent comma, in code, dict literals and
    JSON output alike. A line left blank goes too."""
    for pat in (_ITEM_KEY, _ITEM_REMOVED, _ITEM_IDENT_VALUE):
        text = _drop_items(text, pat)
    return text


def _drop_items(text, pat):
    pos = 0
    for _ in range(500):
        m = pat.search(text, pos)
        if not m:
            break
        back = re.sub(r"(?:\s|(?:\\+n|\n)[+-]?)+$", "", text[:m.start()])
        if back and back[-1] not in "{(,[":
            pos = m.end()
            continue
        end = _value_end(text, m.end())
        if end is None or end == m.end():
            pos = m.end()
            continue
        a, b = m.start(), end
        mc = re.match(r"[ \t]*,[ \t]*", text[b:])
        if mc:
            b += mc.end()
        else:
            mp = re.search(r",(?:\s|\\+n)*$", text[:a])
            if mp:
                a = mp.start()
        text = text[:a] + text[b:]
        ls, le, sep = _line_bounds(text, a)
        if ls > 0 and not re.sub(r"^\s*[+-]?", "", text[ls:le]).strip():
            text = text[:sep] + text[le:]
            a = sep
        pos = a
    return text


def _drop_code_lines(text):
    """Code lines that still carry a removed budget value or a budget identifier ('assert t >= [removed]',
    'save("run_deadline", [removed])') are deleted; a Python block header ('if t < [removed]:') goes with its
    indented body."""
    if REMOVED not in text and not BUDGET_IDENT.search(text):
        return text
    pieces = _LINE_SPLIT.split(text)
    lines = pieces[0::2]
    seps = pieces[1::2] + [""]
    drop = [False] * len(lines)
    i = 0
    while i < len(lines):
        line = lines[i]
        if (REMOVED in line or BUDGET_IDENT.search(line)) and _is_code_line(line) and len(line) <= 600:
            drop[i] = True
            body = _DIFF_MARK.sub("", line, count=1)
            if body.rstrip().endswith(":"):
                ind = len(body) - len(body.lstrip())
                j = i + 1
                while j < len(lines):
                    bj = _DIFF_MARK.sub("", lines[j], count=1)
                    if bj.strip() and len(bj) - len(bj.lstrip()) <= ind:
                        break
                    drop[j] = True
                    j += 1
                i = j
                continue
        i += 1
    if not any(drop):
        return text
    # a dropped assignment used as the duration of a `timeout` wrapper on a kept line (secs=... then
    # `timeout "${secs}s" make test`) only stopped the command at the deadline: the wrapper goes too and the command
    # runs as written
    for i, line in enumerate(lines):
        if not drop[i] or REMOVED not in line or BUDGET_IDENT.search(line):
            continue
        ma = _ASSIGN.match(_DIFF_MARK.sub("", line, count=1))
        if not ma:
            continue
        use = re.compile(r"(?<![\w])\$?\{?" + re.escape(ma.group(1)) + r"(?![\w])")
        wrap = re.compile(r"\btimeout(?:\s+(?:-[ks]\s+[^\s-]\S*|--?[A-Za-z][\w-]*(?:=\S+)?))*\s+(\\*[\"']?)\$\{?"
                          + re.escape(ma.group(1)) + r"\}?[smhd]?\1\s+")
        for j in range(len(lines)):
            if j != i and not drop[j] and use.search(lines[j]):
                lines[j] = wrap.sub("", lines[j])
    # a dropped line that opened a quote closed on the next line (printf 'x\\n' split at the escaped newline) takes
    # the closing quote with it, so the kept text stays balanced
    for i, line in enumerate(lines):
        if drop[i] and i + 1 < len(lines) and not drop[i + 1]:
            for q in ("'", '"'):
                if len(re.findall(r"(?<!\\)" + q, line)) % 2 == 1 and re.match(r"\\*" + q, lines[i + 1]):
                    lines[i + 1] = re.sub(r"^\\*" + q, "", lines[i + 1], count=1)
                    break
    return "".join(l + s for l, s, d in zip(lines, seps, drop) if not d)


_ASSIGN = re.compile(r"^\s*(?:export\s+|local\s+|readonly\s+|const\s+|let\s+|var\s+)?([A-Za-z_]\w*)\s*=(?!=)")


def _drop_prose_removed(text):
    """A prose sentence that still holds a '[removed]' deadline ('The job stops at `[removed]`.', an orphaned
    '`[removed]`.') is deleted; the rest of the line stays."""
    if REMOVED not in text:
        return text
    pieces = _LINE_SPLIT.split(text)
    for i in range(0, len(pieces), 2):
        line = pieces[i]
        if REMOVED not in line or _is_code_line(line):
            continue
        mk = _DIFF_MARK.match(line)
        prefix = mk.group(0) if mk and mk.group(0).strip() else line[:len(line) - len(line.lstrip())]
        body = line[len(prefix):]
        # a sentence the clause step already emptied can leave "...done.[removed] UTC, ..." glued together
        body = re.sub(r"([.!?])(?=\\*[`'\"]?\[removed\])", r"\1 ", body)
        sents = _sentences(body)
        keep = [body[s:e] for s, e in sents if REMOVED not in body[s:e]]
        pieces[i] = prefix + (_cap(" ".join(keep)) if keep else "")
    return "".join(pieces)


@lru_cache(maxsize=None)
def _quick(requested_min):
    """Cheap prefilter: anything that could possibly need an edit."""
    mention = _patterns(requested_min)[0]
    return re.compile("|".join([mention.pattern[4:], BUDGET_PHRASE.pattern[4:], REMAINING.pattern[4:],
                                INSTRUCTION.pattern, DEADLINE_KEY.pattern[4:], BUDGET_IDENT.pattern[4:],
                                _APOLOGY.pattern[4:],
                                r"(?:requested|budget|deadline|elapsed|remaining|minimum|allotted|window|until|full)"]),
                      re.I)


# Sentences that only explain not filling the requested time ("I cannot idle.", "Padding further adds nothing.")
# go whole, across quotes and inline math that would otherwise split them.
_APOLOGY = re.compile(
    r"(?i)no\s+way\s+to\s+(?:\w+\s+)?\\*[\"“]?(?:pause|wait|idle|stall)"
    r"|(?:can['’]?t|cannot|can\s+not)\s+(?:\w+\s+)?(?:stretch|idle)\b"
    r"|don['’]t\s+have\s+a\s+way\s+to\s+(?:idle|pause|wait)"
    r"|make\s+(?:the\s+)?(?:wall[- ]?clock\s+)?(?:time|clock)\s+(?:advance|pass)"
    r"|\bpad(?:ding)?\s+(?:the\s+(?:answer|reasoning|response)|further)\b")
_MATH = re.compile(r"\$[^$\n]{1,160}\$")


def _drop_apologies(text):
    if not _APOLOGY.search(text):
        return text
    pieces = _LINE_SPLIT.split(text)
    for i in range(0, len(pieces), 2):
        line = pieces[i]
        if not _APOLOGY.search(line) or _is_code_line(_MATH.sub("", line)):
            continue
        sents = _sentences(line)
        keep, cut = [], 0
        for s0, e0 in sents:
            sent = line[s0:e0]
            if _APOLOGY.search(sent):
                cut += 1
                continue
            keep.append(sent)
        if cut:
            lead = line[:len(line) - len(line.lstrip())]
            pieces[i] = lead + " ".join(keep)
    return "".join(pieces)


_ONLY_ELAPSED = re.compile(r"(^|(?<=[.!?]\s)|(?<=\n)|(?<=\\n)|(?<=\*\*\s))Only\s+((?:about|roughly|around|approximately|"
                           r"~)?\s*\d[\d.,]*\s*(?:seconds?|secs?|minutes?|mins?|s|min)\b[^.!?\n]{0,80}?\b(?:elapsed|"
                           r"passed)\b)")


def strip_request(text, requested_min, extra_values=()):
    """Remove the requested-duration instruction and every restatement of the budget from one string.
    Text with nothing to remove is returned unchanged (the same object)."""
    if not isinstance(text, str) or not text:
        return text
    R = float(requested_min)
    if not _quick(R).search(text) and not any(v in text for v in extra_values):
        return text
    flags = {}                                         # set by the clause step: "_cont", "_using_rewrite"
    # 1. the instruction
    new = INSTRUCTION.sub("", text)
    # 2-3. deadline values, bare numbers
    new = _replace_tokens(new, R, extra_values)
    new = _drop_apologies(new)
    # code around the budget (derived xtrace values, budget-keyed items, guard and deadline lines)
    new = _taint_xtrace(new)
    new = _drop_budget_items(new)
    new = _drop_code_lines(new)
    # 4-5. phrases, clauses, sentences, per hard-bounded segment
    # a segment the removal empties is marked (_EMPTIED), and so is a sentence cut at a line end that runs
    # on to the next line (_CONT); _resolve_sentinels then removes what the cut left behind (an empty docstring,
    # print(''), explanation:"", an empty table cell, an emptied output line, the orphan tail of a wrapped sentence)
    marks = _EMPTIED not in new and _CONT not in new
    pieces, pos = [], 0
    for m in list(_HARD.finditer(new)) + [None]:
        seg = new[pos:m.start()] if m else new[pos:]
        if seg.strip():
            res = _strip_segment(seg, R, flags)
            cont = flags.pop("_cont", 0)
            if marks and res != seg:
                if not res.strip():
                    res = _EMPTIED
                elif cont and m is not None and re.match(r"\\+n|\r?\n", m.group(0)):
                    res = res.rstrip(" ") + _CONT
            seg = res
        pieces.append(seg)
        if m is not None:
            pieces.append(m.group(0))
            pos = m.end()
    new = "".join(pieces)
    if flags.pop("_using_rewrite", 0):
        # after the "using" rewrite, "I’ll diff `Y`, and to check Z" loses its second "to" (across inline code, so
        # on the whole text)
        new = _AND_TO.sub(r"\1 ", new)
    if marks:
        new = _resolve_sentinels(new)
    new = _drop_prose_removed(new)
    if new != text:
        # in a text the removal edited, "Only 40 seconds have elapsed" measured the time against the removed
        # budget; the measurement stays, the contrast goes
        new = _ONLY_ELAPSED.sub(lambda m: m.group(1) + _cap(m.group(2)), new)
    if not new.strip():
        new = ""                                       # an emptied output is empty, not a lone newline
    return text if new == text else new


def deadline_values(text):
    """Values found under deadline-like keys (epochs and ISO timestamps), for removal elsewhere in the text."""
    return {m.group(2) for m in DEADLINE_KEY.finditer(text)} if isinstance(text, str) else set()


def _ts_seconds(v):
    """Epoch seconds of a timestamp spelling (ISO, 'YYYY-MM-DD HH:MM:SS[ UTC]', epoch s or ms); naive = UTC."""
    v = v.strip()
    if re.fullmatch(_TS_EPOCH, v):
        x = float(v)
        return x / 1000 if x > 1e11 else x
    m = re.fullmatch(r"(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d(?:\.\d+)?)(Z|[+-]\d\d:?\d\d|\s?UTC)?", v)
    if not m:
        return None
    tz = (m.group(3) or "Z").strip()
    off = timedelta(0)
    if tz not in ("Z", "UTC"):
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        off = sign * timedelta(hours=hh, minutes=mm)
    try:
        dt = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}").replace(tzinfo=timezone.utc) - off
    except ValueError:
        return None
    return dt.timestamp()


def _ts_spellings(sec):
    """Other spellings of a deadline instant: epoch s / ms, ISO with Z or +00:00, 'YYYY-MM-DD HH:MM:SS[ UTC]', and the
    bare clock 'HH:MM:SS' ('stops at 10:12:00 UTC')."""
    if sec is None or abs(sec - round(sec)) > 1e-3:
        return set()
    dt = datetime.fromtimestamp(round(sec), timezone.utc)
    return {str(int(round(sec))), str(int(round(sec)) * 1000), dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"), dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
            dt.strftime("%Y-%m-%d %H:%M:%S"), dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.strftime("%H:%M:%S")}


def session_deadline_values(obj, requested_min):
    """Deadline instants of a whole session, in every spelling, for removal wherever they appear:
      * values under deadline-like keys (DEADLINE_KEY: run_deadline, min_finish, cutoff_epoch, ...);
      * values under any end-like key (jobEnd, end_at) that lie exactly the budget (+-1 s) after a value under a
        start-like key (jobStart, start_at).
    Each instant is also removed in its other spellings (epoch, ISO, 'HH:MM:SS')."""
    R = float(requested_min)
    vals, starts, ends = set(), [], []
    for s in _leaves(obj):
        low = s.lower()
        if any(w in low for w in ("deadline", "cutoff", "finish", "minimum", "min_end", "end_by", "must_end", "target")):
            vals |= {v for v in deadline_values(s) if len(v) >= 9}
        if "end" in low or "stop" in low or "until" in low or "due" in low or "expir" in low or "limit" in low:
            for m in _KEYED_TS.finditer(s):
                key, sec = m.group("key"), _ts_seconds(m.group("val"))
                if sec is None:
                    continue
                if _START_KEY.search(key):
                    starts.append(sec)
                elif _END_KEY.search(key):
                    ends.append((m.group("val"), sec))
        if "start" in low or "begin" in low or "began" in low:
            for m in _KEYED_TS.finditer(s):
                if _START_KEY.search(m.group("key")):
                    sec = _ts_seconds(m.group("val"))
                    if sec is not None:
                        starts.append(sec)
    starts = sorted(set(starts))
    for v, sec in ends:
        if any(abs(sec - st - R * 60) <= 1.0 for st in starts):
            vals.add(v)
    out = set(vals)
    for v in vals:
        out |= _ts_spellings(_ts_seconds(v))
    return tuple(sorted(out, key=len, reverse=True))


def _leaves(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _leaves(v)


def strip_request_obj(obj, requested_min, extra_values=None):
    """Apply strip_request to every string leaf of a JSON-like value (dict keys are left alone).
    Deadline values found anywhere in the object are also removed wherever else they appear in it.
    Containers are rebuilt; unchanged strings are the same objects."""
    if extra_values is None:
        extra_values = session_deadline_values(obj, requested_min)

    def walk(o):
        if isinstance(o, str):
            return strip_request(o, requested_min, extra_values)
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o
    return walk(obj)
