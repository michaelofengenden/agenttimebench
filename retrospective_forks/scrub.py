"""Time-cue scrub for the R-scrubbed condition.

scrub_messages(api_messages, requested_min, year) takes the rebuilt transcript ([{"role", "content"}], the final
question not included; a tool call reads "[tool_call name=X id=Y]\\n<args>", a tool result "[tool_result id=Y]\\n<output>")
and returns the same list with every time cue removed; emptied messages are kept as "" for the caller to drop.

Cue categories (Rule.cat):
  requested_duration  the task prompt's duration instruction and every restatement of the budget.
  clock_date          timestamps and dates in any format, the session year, clock-tool calls (call and result go as a
                      pair), date commands, clock reads in code and in stat/find/ps/ls options.
  duration_wait       measured durations and waits (12.5s, 1h05m, progress-bar timings, rates, time(1) output, CPU
                      counters, sleep/timeout arguments, exit status 124); sleep calls, waiting loops, and polls that
                      return nothing new.
  agent_time_talk     the agent's talk about its budget, remaining time, pacing, waiting and slowness.

How:
  * Agent prose: a sentence with a cue of any category is dropped whole, so prose never carries a placeholder. Code
    fences get the code treatment; table rows and dict lines are scrubbed in place.
  * Tool calls, tool results, system text: JSON bodies are decoded (recursively) so time-named keys go with their
    values and clock/sleep items are dropped. Every string then goes through scrub_text: budget clauses rewritten,
    sleep/date/clock statements removed, bookkeeping lines dropped, budget-talk sentences dropped, every remaining cue
    replaced by "[removed]", then label shells and lines left with nothing but removed values dropped.
  * The task prompt loses only the duration instruction and clock/date cues.
  * Message headers are never edited. A clock or sleep tool call, or a Codex exec call whose command was only a sleep,
    is dropped together with its result. Prose messages that became neighbours because the pairs between them were
    dropped are merged (the final answer stays its own message), and a chain of polls of one running command
    collapses into its first poll.

Rules and branches of the scrubber as run that never fired on the paper's 69 parent sessions are left out; on those
sessions this module's output is byte-identical to the R-scrubbed requests that were sent.
"""
import json
import re

REMOVED = "[removed]"
_RM = re.escape(REMOVED)


# --- vocabulary
_WORDNUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "a": 1, "an": 1, "half": 0.5,
    "quarter": 0.25, "couple": 2, "few": 3, "several": 3,
}
_NUMWORD = (r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
            r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)")
_WORDS = _NUMWORD + r"(?:[- ]" + _NUMWORD + r")*"
_DIGITS = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_FRACT = r"(?:\s+and\s+(?:a\s+)?(?:half|quarter|third)|\s*[½¼¾])"
_QTY = (r"(?:(?:" + _DIGITS + r"|" + _WORDS + r")" + _FRACT + r"?"
        r"|(?:a|an)\s+(?:few|couple(?:\s+of)?)|(?:a\s+)?(?:few|several|couple\s+of)|half\s+an?|an?(?:\s+and\s+a\s+half)?"
        r"|(?:a\s+)?quarter(?:\s+of)?\s+an?)")
# Long unit words. "second" singular is only a unit after a digit or number word (not "a second run").
_UNIT = (r"(?:milliseconds?|millisecs?|msecs?|ms|microseconds?|µs|nanoseconds?|seconds|secs?|minutes?|mins?|"
         r"hours?|hrs?|hr)")
_UNIT_S = r"(?:second)"
_DUR_ATOM = (r"(?:(?:" + _DIGITS + r"|" + _WORDS + r")" + _FRACT + r"?\s*[-‐]?\s*(?:" + _UNIT + r"|" + _UNIT_S +
             r")\b|" + _QTY + r"(?:\s+|\s*[-‐]\s*)(?:" + _UNIT + r")\b)")     # "a minute", never "TeX-AMS"
_DUR = (r"(?:(?:" + _DIGITS + r"|" + _WORDS + r")\s*(?:-|–|—|to|or|of|/|out\s+of)\s*)?" + _DUR_ATOM +
        r"(?:\s*(?:,|and)?\s*(?:" + _DIGITS + r")\s*(?:" + _UNIT + r"|" + _UNIT_S + r"|s|m)\b)*"
        r"(?:\s+and\s+a\s+(?:half|quarter)\b)?")
# Attached single-letter units: 2h55m, 1m30s, 0m12.345s, 3.2s; bare integer+s/m/h only in duration context.
_ATTACHED = r"(?:\d+\s?h\s*\d+\s?m(?:in)?\b(?:\s*\d+(?:\.\d+)?\s?s\b)?|\d+h\s*\d+(?:\.\d+)?s|\d+m\s*\d+(?:\.\d+)?s|\d+(?:\.\d+)?h\b)"
_CTX_BEFORE = r"(?:\s-|\bin|\btook|\bafter|\bevery|\bfor|\bwithin|\bover|~|≈|\beta:?|\bETA:?|\belapsed:?|=|:|\bsleep|\bwait|\btimeout|\[removed\],?)"
_RUNTIME = (r"(?<![-_/.])(?<!environment\s)(?<!container\s)(?<!language\s)(?<!Python\s)(?<!python\s)(?<!Java\s)"
            r"(?<!CUDA\s)(?<!Docker\s)(?<!\bR\s)\bruntimes?\b(?![-_.]\w)(?!\s+(?:environment|pieces?|dependenc\w*|librar\w*|packages?|errors?|"
            r"versions?|image|config\w*|components?|deps|warnings?|installation|setup|stack|not|api|type|check|facts?|notes?|"
            r"info|details|requirements?|system))")
# qualitative duration talk: slowness, long runs, waiting (agent prose and agent-written notes)
_SLOW = (r"\bslow(?:ly|er|est|ness|s|ed|ing)?\b|\blong[- ]running\b|\b(?:the|a|this|that)\s+long\s+(?:run|part|tail|job|phase|"
         r"computation|wait|process|haul|pole)\b|\btail\s+end\b|\bquite\s+a\s+while\b|\bfor\s+a\s+while\b|\btakes?\s+a\s+while\b"
         r"|\bin\s+th(?:is|at|e\s+last)\s+interval\b|\b(?:continued|ongoing|further|passive)\s+monitoring\b"
         r"|\bthe\s+monitor\s+(?:exits|finishes|completes|ends|stops|is\s+done)\b|\b(?:I['’]m|I\s+am|we['’]re)\s+(?:still\s+)?waiting\b"
         r"|\bwait(?:ing|ed)?\s+(?:for|on|until)\s+(?:it|them|the|this|that|its|their|both|all|each|every)\b"
         r"|\blet(?:ting)?\s+(?:it|them)\s+(?:run|finish|complete|continue)\b|\breal\s+time\b|\bto\s+time\s+(?:them|it|each|the|this)\b"
         r"|\bspeed-?ups?\b|\blong\s+(?:[\w-]+\s+){0,2}(?:run|runs|job|jobs|training|computation|process|render)\b"
         r"|\b(?:begin|start|finish|complete|end|exit|be\s+(?:done|ready))\s+soon\b|\bas\s+soon\s+as\b")

_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
_MONTH = (r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
          r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?")
_WDAY = r"(?:Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun)(?:day|sday|nesday|urday)?"
_TZ = r"(?:\s*(?:Z\b|UTC|GMT|Etc/UTC|[+-][01]\d:?[0-5]\d\b|[ECMP][SD]T\b|[AP]\.?M\.?\b|[ap]\.?m\.?(?!\w)))?"
_CLOCK_HMS = r"(?:[01]?\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+|,\d{3}(?![\d-]))?"
_RECENT_YEAR = r"202[4-7]"                              # years that date the session (not the task's data)

# --- budget talk (sentence triggers)
BUDGET = re.compile(r"(?ix)"
    r"\brequested\s+(?:[\w’'-]+\s+){0,3}?(?:period|window|interval|time|duration|session|timeframe|time-?box|budget|minutes?|hours?)\b"
    r"|\b(?:period|window|interval|time|duration|minutes?|session)\s+(?:that\s+)?you\s+(?:requested|asked\s+for|specified|gave)"
    r"|\b(?:work(?:ing)?|review|task|full|entire|whole|allotted|allocated|assigned|given|audit|verification)\s+"
    r"(?:period|window|interval|budget|box|limit|allotment|allowance)\b"
    r"|\btime-?box(?:ed|ing)?\b"
    r"|\b(?:remaining|rest\s+of\s+(?:the|my|your)|remainder\s+of\s+(?:the|my|your))\s+"
    r"(?:\w+[- ]?){0,2}?(?:time|period|window|session|interval|minutes?|seconds?|hours?|budget)\b"
    r"|\btime\s+(?:remaining|left|elapsed|used|check|budget|limit|is\s+up|allocation|allotment|allowance|window|"
    r"record|reading|readings|stamp|stamps|frame)\b"
    r"|\b(?:minutes?|seconds?|hours?|time)\s+(?:remain|remaining|left|elapsed|have\s+passed|has\s+passed|had\s+passed|into)\b"
    r"|\belapsed\b|\bwall[- ]?clock\b|\bwall[ _]?time\b"
    r"|\b(?:the|a|my|your)\s+clock\b|\bcurrent\s+time\b|\b(?:check|checking|checked|note|noting|noted|record|recording|track|tracking)\s+(?:the\s+)?(?:time|clock)\b"
    r"|\btimer\b|\bstopwatch\b|\bhalfway\b|\bdeadline\b|\btime'?s\s+up\b|\bout\s+of\s+time\b"
    r"|\buntil\s+the\s+(?:\w+[- ]?){0,2}?(?:period|window|interval|session|time|timer|deadline)\s+(?:ends|is\s+up|expires|elapses|is\s+complete|completes|closes|runs\s+out)"
    r"|\b(?:period|window|interval)\s+(?:ends|is\s+up|expires|has\s+elapsed|elapses|is\s+complete|completes|closes|runs\s+out|approaches)"
    r"|\bhold(?:ing)?\s+(?:the\s+|my\s+|this\s+)?(?:submission|response|answer|final\s+answer|handoff)"
    r"|\bstretch\s+(?:this|it|the\s+task)\b|\bpad(?:ding)?\s+(?:the|my)\s+time|\bfill\s+the\s+(?:remaining|time|rest)"
    r"|\bno\s+way\s+to\s+(?:pause|wait|sleep)|\b(?:planned|scheduled|fixed|time|its|repeat['’]s)\s+cut-?off\b|\bcut-?off\s+(?:time|epoch|utc)\b"
    r"|\b(?:start|end|finish|stop)\s+time\b(?![-\w])|\bwork\s+session\b|\bsession\s+time\b"
    r"|\brequested[- ]duration\b|\b(?:final|last|remaining|spare|extra|closing)\s+(?:few\s+|couple\s+of\s+)?(?:minutes?|seconds?|hours?)\b"
    r"|\bclock\s+(?:readings?|calls?|checks?|times?|reads?)\b"
    r"|\bwait(?:ing)?\s+out\b|\bkeep(?:ing)?\s+(?:the\s+)?(?:session|window|task)\s+open\b"
    # budget words without a number
    r"|\b(?:allotted|allocated|assigned|available|given|budgeted|remaining)\s+time\b"
    r"|\btime[- ]window\b|\b(?:observation|completion|audit|review|work|monitoring)\s+window\b"
    r"|\bscheduled\s+to\s+(?:stop|end|finish|terminate|close)\b"
    r"|\b(?:monitoring|polling|waiting|idle)\s+(?:cycles?|rounds?|loops?|intervals?)\b"
    r"|\bscheduled\s+(?:checks?|handoff|stop|end)\b|\bfinal\s+handoff\b"
    r"|\bcadence\b|\b(?:observed|established|expected|current|steady|same|this)\s+pace\b"
    r"|\brepeated\s+(?:\w+\s+)?intervals\b|\b(?:completed|timed|measured)\s+interval\b"
    r"|\bfinal\s+(?:part|phase|stretch|portion|minutes)\s+of\s+(?:the|my|this)\b|\bfinal\s+(?:audit|review|verification)\s+phase\b"
    r"|\bruntime\s+(?:estimate|projection|budget)s?\b|\b(?:faster|slower|expected|projected|estimated|total|full|new)\s+runtime\b"
    r"|\b(?:the|its)\s+runtime\s+(?:is|was|appears|remains|seems)\b|\bruntime\s+is\s+(?:heavy|long|short|high)\b"
    r"|\btimeline\b|\bcurrent\s+year\b|\b(?:full|total)\s+(?:work\s+)?duration\b|\bwork\s+duration\b"
    r"|\btakes?\s+(?:far\s+)?(?:longer\s+than\s+)?(?:hours|minutes|seconds|a\s+while|long)\b"
    r"|\bfinish(?:es)?\s+in\s+(?:seconds|minutes|hours)\b|\btimeUsed\w*|\btime_used\w*"
    r"|\b(?:expected|estimated|projected|scheduled)\s+(?:\w+\s+)?(?:time|window)\b|\bcompletion\s+time\b"
    r"|\b(?:start|finish|end)\s+(?:and|&)\s+(?:finish|end|stop)\s+times?\b"
    r"|\btakes?\s+(?:far\s+|much\s+)?longer\b|\bin\s+(?:mere\s+|just\s+)?(?:seconds|minutes|hours)\b|\b(?:takes?|took|taking)\s+(?:\w+\s+){0,2}(?:hours|minutes|seconds)\b"
    r"|" + _RUNTIME +
    # time-boxed runs, timeout limits, named run/compute times, still-running waits
    r"|\btimed\s+(?:run|job|process|execution|repeat|experiment|session|attempt)s?\b"
    r"|\bplanned\s+(?:limit|stop|end|cutoff|cut-off)\b|\btimeout\s+exit\s+(?:code|status)\b|\bexit\s+(?:code|status)\s+(?:of\s+)?124\b"
    r"|(?<!\bat )(?<!\bAt )\b(?:render|rendering|build|compile|compilation|execution|compute|computation|cpu|processing|"
    r"download|training|wall|run|solve|wait|waiting|idle)\s+times?\b"
    r"|\bstill\s+(?:genuinely\s+|actively\s+|busy\s+)?(?:running|executing|processing|computing|in\s+progress|going|working\s+on)\b"
    r"|\b(?:continu\w+|keep\w*)\s+to\s+wait\b|\bwait(?:ing)?\s+for\s+(?:its\s+|the\s+)?(?:genuine\s+|full\s+|natural\s+)?completion\b"
    # wait advice in the Codex system prompt (longer waits, busy polling)
    r"|\blonger\s+waits\b|\bbusy[- ]polling\b")
# waiting on a running job, background monitoring, progress countdowns, pacing announcements
_WAITING = (r"\bmonitor(?:ing|ed)\b|\brepeated\s+(?:checks|polls|samples|verifications?|probes|renders)\b"
            r"|\bcontinues?\s+to\s+(?:match|pass|hold|be\s+(?:stable|unchanged|clean|identical))\b"
            r"|\b(?:is|are|was|were|remains?)\s+still\s+(?:running|executing|processing|computing|compiling|installing|building|"
            r"downloading|loading|matching|training|rendering|fitting|sampling|optimi[sz]ing|working|active|going|busy|pending|"
            r"in\s+progress|underway|ongoing|unpacking|resolving|waiting)\b"
            r"|\bstill\s+(?:active|busy|underway|ongoing|pending|compiling|installing|building|downloading|training|rendering|"
            r"fitting|sampling|unpacking|resolving|matching|loading|computing|executing)\b"
            r"|\b(?:is|are)\s+(?:now\s+)?(?:finishing|completing|wrapping\s+up|nearing\s+completion)\b"
            r"|\bactively\s+\w+ing\b|\bprogressing\b|\bin\s+progress\b|\bunderway\b"
            r"|\bremains?\s+(?:active|blocked|busy|running|alive|in\s+progress)\b|\bCPU-bound\b|\bno\s+new\s+output\b"
            r"|\bhas\s+not\s+(?:stalled|hung|yet\s+finished|yet\s+completed|exited)\b"
            r"|\bwhile\s+(?:the|it|they|this|that|those|these|its|their|both|all)\s+(?:[\w-]+\s+){0,4}?(?:runs?|compiles?|compile|"
            r"completes?|finish(?:es)?|continues?|installs?|builds?|downloads?|executes?|renders?|trains?|proceeds?|is\s+running|"
            r"are\s+running)\b"
            r"|\bcomputationally\s+(?:heavy|expensive|intensive|demanding)\b|\blonger\s+(?:run|runs|job|computation|process|wait)\b"
            r"|\b(?:reduc\w*|lower\w*|decreas\w*|smaller|shorter|fewer)\b[^.\n]{0,60}\bfor\s+speed\b"
            r"|\b(?:training|run|execution|inference|compute)[- ]speed\b|\bspeed\s+(?:benchmark|test)\b"
            r"|\bI['’]m\s+(?:now\s+)?(?:at|in)\s+the\s+(?:final|closing|last)\b|\bclosing\s+(?:integrity|validation|verification)\b"
            r"|\blast\s+integrity\s+pass\b|\bclosing\s+checks\b|\b(?:epoch|step|iteration|cell)\s+\d+\s+is\s+(?:now\s+)?(?:running|computing|training)\b"
            r"|\b(?:is|are)\s+(?:now\s+)?(?:installing|compiling|downloading)\b"
            r"|\b(?:execution|run|job|process|training|render|script|computation|install(?:ation)?)\s+continues\b"
            r"|\bleaving\s+(?:only\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|\w+teen|twenty[\w-]*|thirty[\w-]*|"
            r"forty[\w-]*|fifty[\w-]*)\s+(?:more\s+)?(?:epochs?|steps?|iterations?|cells?|scripts?|notebooks?|jobs?)\b"
            r"|\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\w+teen|twenty[\w-]*|thirty[\w-]*|"
            r"forty[\w-]*|fifty[\w-]*)\s+(?:more\s+)?(?:epochs?|steps?|iterations?|cells?|scripts?|notebooks?|jobs?|events?)\s+"
            r"(?:remain|remaining|left|to\s+go)\b|\bremaining\s+\d+\s+(?:epochs?|steps?|iterations?)\b|\b(?:epochs?|steps?)\s+(?:remain|left)\b")
# Padding-loop progress narration ("Health probe 7 passed.", "the 4th sanity check").
_COUNTER_TALK = (r"\b(?:integrity|stability|consistency|monitor(?:ing)?|health|sanity|verification|artifact)\s+"
                 r"(?:check|sample|poll|probe|snapshot)\s*#?\d+\b"
                 r"|\b(?:check|poll|probe|sample|snapshot)\s+#?\d+\s+(?:remains|passed|confirms|is|was|shows|completed|still|"
                 r"reports|found)\b"
                 r"|\b\d+(?:st|nd|rd|th)\s+(?:\w+\s+){0,2}(?:check|poll|probe|sample|snapshot)\b"
                 r"|\b(?:through|after|at|by|since|until|past|on)\s+(?:check|poll|probe|sample|snapshot|iteration|cycle|round)\s+#?\d+\b"
                 r"|\b(?:check|poll|probe|sample|snapshot|iteration|cycle)\s+#\d+\b")
# Only in agent prose: softer words that still announce time bookkeeping ("On pacing: ...").
PROSE_ONLY = re.compile(r"(?i)\btiming\b|\bpacing\b|\bclock\b|\b(?:minutes|hours|seconds)\b"
                        r"|\b(?:a|an|the|one|final|last|next|first|each|per|every)\s+(?:minute|hour)\b|\btime\s+(?:budget|management|tracking)\b|\bin\s+time\b|\bon\s+time\b"
                        r"|\btimestamps?\b|" + _RUNTIME +
                        r"|\bshortly\b|\bany\s+moment\b|\blonger\s+waits\b|" + _SLOW + "|" + _COUNTER_TALK + "|" + _WAITING)

# Vague time talk ("This could take hours.") in agent-written natural language inside tool calls (notes, plans,
# goal objectives). Only for sentences of 3+ words with no code punctuation, so `print('CPU seconds:', ...)` stays.
VAGUE = re.compile(r"(?i)\b(?:minutes|hours|seconds)\b|\b(?:a|an|the|one|final|last|next|first|each|per|every)\s+"
                   r"(?:minute|hour)\b|\btimestamps?\b|\bdurations?\b|\btiming\b|\bpacing\b|\bpace\b|" + _RUNTIME +
                   "|" + _SLOW + "|" + _WAITING +
                   # urgency in agent notes ("Launch the sweep immediately.")
                   r"|\b(?:start|launch|run|kick\s+off|begin)\b[^.\n]{0,40}\bimmediately\b|\bimmediately\s+(?:start|launch|run|kick|begin)")
_PROSE_WORD = re.compile(r"(?:^|(?<=\s))[A-Za-z][a-z’'-]*(?=[\s,.;:!?]|$)")
_CODEISH = re.compile(r"[={}<>$]|::|\bdef\b|\bf['\"]|[A-Za-z_]\(|\bimport\b|\breturn\b|\\[nt]")

# "use the full 12-minute task period to lint ..." -> "lint ...": keeps the rest of a goal/plan sentence.
CLAUSE = re.compile(r"(?i)\b(?:use|spend|using|spending)\s+(?:the\s+|my\s+|your\s+)?(?:(?:full|entire|whole|requested)\s+)*"
                    + _DUR + r"(?:\s+(?:task|work|review|working))?(?:\s+(?:period|window|session|interval|budget))?"
                    r"\s+to\s+(?=[a-z])")


# --- token rules
class Rule:
    __slots__ = ("name", "cat", "pattern", "group", "action")

    def __init__(self, name, cat, pattern, group=0, action="token"):
        self.name, self.cat, self.group, self.action = name, cat, group, action
        self.pattern = re.compile(pattern) if pattern is not None else None


_TIME_KEY_TOKENS = {
    "time", "times", "timestamp", "timestamps", "ts", "date", "dates", "datetime", "elapsed", "duration", "durations",
    "seconds", "secs", "sec", "ms", "millis", "milliseconds", "minutes", "mins", "hours", "hrs", "timeout",
    "timeouts", "sleep", "delay", "interval", "eta", "deadline", "epoch", "mtime", "ctime", "atime", "birthtime",
    "utc", "tz", "timezone", "clock", "uptime", "runtime", "walltime", "yield", "wait", "ttl", "at", "when",
    "modified", "created", "updated", "started", "ended", "finished", "completed", "expires", "expiry", "cutoff",
    "timing", "timings", "utime", "stime", "usec",
}
# keys whose whole value (dict or list included) is time bookkeeping: {"timing": {...}}
_TIME_CONTAINER = re.compile(r"(?i)^(?:\w*[_-])?(?:timing|timings|timestamps|durations|elapsed|clock|times|timer|timers|"
                             r"deadline|deadlines|schedule|session_timing|work_period)$")
_NOT_TIME_KEY = {"cat", "format", "data", "state", "update", "at_least", "wait_agent", "date_type", "datetype", "ms"}
# budget bookkeeping keys ("requested_secs", "work_window", "end_utc")
_BUDGET_KEY = re.compile(r"(?i)requested_(?:minutes?|seconds?|mins?|secs?|duration|work|task_period|period|time|window|"
                         r"minimum|end)|full_requested|work_?(?:period|interval|window|minutes|seconds)|deadline|cutoff|"
                         r"elapsed|remaining_(?:seconds|minutes|time|secs|mins)|time_?(?:left|remaining|used)|timeUsed|"
                         r"_utc$|^utc_|minimum_(?:finish|end)|started|finished|current_year|"
                         r"tokensUsed|remainingTokens|completionBudget\w*|tokenBudget|timeBudget")


def is_time_key(name):
    low = name.lower().strip("-_.")
    if low in _NOT_TIME_KEY or not low:
        return False
    parts = [p for p in re.split(r"[_\-.]+|(?<=[a-z0-9])(?=[A-Z])", name) if p]
    toks = [p.lower() for p in parts]
    if toks and toks[-1] == "type":
        return False
    return any(t in _TIME_KEY_TOKENS for t in toks) or bool(re.search(r"(?i)time|elapsed|duration|timeout|epoch", low))


_STRONG_TIME_TOKENS = {"seconds", "secs", "millis", "milliseconds", "minutes", "mins", "hours", "hrs",
                       "elapsed", "duration", "durations", "runtime", "walltime", "uptime"}   # not "sec" (\\label{sec:x}), "ms", "eta"


def is_strong_time_key(name):
    """Keys that name a duration whatever their value is: {'seconds': float(x)}, elapsed_s=..."""
    toks = [p.lower() for p in re.split(r"[_\-.]+|(?<=[a-z0-9])(?=[A-Z])", name) if p]
    return bool(toks) and toks[-1] != "type" and any(t in _STRONG_TIME_TOKENS for t in toks)


def is_budget_key(name):
    return bool(_BUDGET_KEY.search(name))


_LITERAL = re.compile(r"-?\d[\d.eE+\-]*(?:\s*(?:ms|s|m|h|sec|secs|min|mins))?|\\*[\"'][^\"'\n]*(?:\d|UTC|GMT|/|\[removed\])"
                      r"[^\"'\n]*\\*[\"']|\[removed\]|None|null|NULL|NA")


def _pair_value_ok(key, value):
    """Budget keys go with any value; other time keys only with a literal time-like value (not `'time': row.t`)."""
    if is_budget_key(key):
        return True
    if isinstance(value, bool):
        return False
    if value is None or isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return bool(_LITERAL.fullmatch(value.strip())) or bool(re.search(r"(?i)\d|utc|gmt|/|\[removed\]", value))
    return False


def _pair_key(name):
    """Keys whose key/value pair is deleted whole (not just the value)."""
    return (is_time_key(name) or is_budget_key(name)) and name.lower() not in ("at", "when", "ts", "tz", "date")


_KEY_RE = re.compile(
    r"(?<![\w$])(?P<key>[A-Za-z_][\w\-]{0,40}?)(?P<sep>\\*[\"']?\s*(?:[:=]|=>)\s*)"
    r"(?P<val>\\*\"[^\"\\\n(),;]{0,60}?\\*\"|'[^'\n(),;]{0,60}'|(?:\d+-)?\d{1,2}(?::\d{2}){1,2}(?:\.\d+)?(?![\d:])|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?:\s*(?:ms|s|m|h|sec|secs|min|mins)\b)?)")

# order matters: composite dates before their pieces, env lines and wall-time lines before generic durations
TOKEN_RULES = [
    # ---- category 2: clocks and dates
    Rule("codex_env_date_tz", "clock_date",
         r"[ \t]*<(current_date|timezone)>[^<\n]*</\1>[ \t]*(?:\r?\n|\\+n)?", 0, "drop"),
    Rule("uuid7_timestamp_id", "clock_date",
         r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])"),
    Rule("exif_datetime", "clock_date",
         r"(?<![\d:])(?:19|20)\d{2}:[01]\d:[0-3]\d(?:[ T][0-2]\d:[0-5]\d(?::[0-5]\d)?(?:\.\d+)?(?:[+-][01]\d:?[0-5]\d|Z)?)?(?![\d:])"),
    Rule("date_cmd_or_git", "clock_date",
         r"\b" + _WDAY + r",?\s+" + _MON + r"\s+\d{1,2}\s+" + _CLOCK_HMS + _TZ + r"(?:\s+[A-Z]{2,5})?\s+\d{4}(?:\s+[+-]\d{4}|\s+(?:UTC|GMT)\b)?"),
    Rule("rfc_date", "clock_date",
         r"\b" + _WDAY + r",\s+\d{1,2}\s+" + _MON + r"\s+\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?" + _TZ + r")?"),
    Rule("iso_datetime", "clock_date",
         r"(?<![\d])(?:1[89]|20)\d{2}([-/_])(?:0?[1-9]|1[0-2])\1(?:0?[1-9]|[12]\d|3[01])"
         r"(?:[T _\-](?:[01]\d|2[0-3])[:\-][0-5]\d(?:[:\-][0-5]\d(?:\.\d+|,\d{3}(?![\d-]))?)?)?" + _TZ + r"(?![\d])"),
    Rule("compact_date_stamp", "clock_date",
         r"(?<![\d.])20[12]\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:[T_\-]?[0-2]\d[0-5]\d(?:[0-5]\d)?Z?)?(?![\d])"),
    Rule("calver_version", "clock_date",
         r"(?<![\w.])" + _RECENT_YEAR + r"\.(?:0?[1-9]|1[0-2])(?:\.\d{1,2})?(?:\.post\d+)?(?![\w]|\.\d)"),
    Rule("year_month", "clock_date",
         r"(?<![A-Za-z0-9.])" + _RECENT_YEAR + r"[-_/](?:0[1-9]|1[0-2])(?![\d])(?![-_/]\d)"),
    Rule("dmy_date", "clock_date",
         r"(?<![\d/])(?:0?[1-9]|[12]\d|3[01])[/.](?:0?[1-9]|1[0-2])[/.](?:19|20)\d{2}(?![\d/])"),
    Rule("mdy_date", "clock_date",
         r"(?<![\d/])(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:19|20)?\d{2}(?![\d/])"),
    Rule("text_date", "clock_date",
         r"(?:\b" + _WDAY + r",?\s+)?(?:\b\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTH + r",?\s+\d{4}\b"
         r"|\b" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b)"),
    Rule("month_year", "clock_date",
         r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|"
         r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?,?\s+" + _RECENT_YEAR + r"\b"),
    Rule("ls_date", "clock_date",
         r"(?:\b" + _WDAY + r"\s+)?\b" + _MON + r"\s+\d{1,2}\s+(?:\d{1,2}:\d{2}(?::\d{2})?|\d{4})\b"),
    Rule("month_day", "clock_date",
         r"(?:\b" + _WDAY + r",?\s+)?\b" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?\b(?![.:]\d)"),
    # ---- dates in URLs, relative ages, non-English dates, am/pm clocks, day-month dates
    Rule("url_year_month", "clock_date",
         r"(?<=/)(?:19|20)\d{2}[-/](?:0[1-9]|1[0-2])(?:[-/]?(?:0[1-9]|[12]\d|3[01]))?(?=/)"),
    Rule("relative_age", "clock_date",
         r"(?i)(?<![\d,.])\b(?:\d{1,2}(?:\.\d+)?|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|a\s+few|few)"
         r"\s+(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|yrs?)\s+ago\b"
         r"|(?<=:\s)(?:last|this)\s+(?:week|month|year)\b|(?i:(?<=Crawled:\s)|(?<=Published:\s)|(?<=Updated:\s))"
         r"(?:yesterday|today)\b"),
    Rule("cjk_date", "clock_date",
         r"\d{2,4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]"
         r"|(?:周|星期|礼拜)[一二三四五六日天]|农历\s*[正一二三四五六七八九十冬腊]+月[初十廿三]?[一二三四五六七八九十]*"
         r"|\d{1,2}\s*[时時点點]\s*\d{1,2}\s*分|\d+\s*(?:秒|分钟|分鐘|小时|小時|天|周|个月|個月|年)前(?:更新|发布|發布)?"),
    Rule("cyrillic_date", "clock_date",
         r"(?i)\b\d{1,2}\s+(?:янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек)[а-я]*\.?(?:\s+\d{4}(?:\s*(?:г\.|года?))?)?"
         r"(?:\s+в\s+\d{1,2}:\d{2})?"),
    Rule("euro_month_date", "clock_date",
         r"(?i)\b\d{1,2}\.?\s+(?:Januar|Februar|März|Maerz|Mai|Juni|Juli|Oktober|Dezember|janvier|février|mars|avril|mai|"
         r"juin|juillet|août|septembre|octobre|novembre|décembre|enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
         r"septiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?\d{4}\b"),
    Rule("ampm_time", "clock_date",
         r"(?<![\w.:])(?:[01]?\d|2[0-3])(?:[:.][0-5]\d)?\s?(?:[aApP]\.?[mM]\.?)(?![A-Za-z0-9])"),
    Rule("weekday_ordinal", "clock_date",
         r"\b(?:Mon|Tues?|Wed(?:nes)?|Thu(?:rs)?|Fri|Sat(?:ur)?|Sun)day,?\s+(?:the\s+)?\d{1,2}(?:st|nd|rd|th)\b"
         r"|\b\d{1,2}(?:st|nd|rd|th)\s+of\s+" + _MONTH),
    Rule("day_month", "clock_date",
         r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|"
         r"December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?(?![\w])(?!\s*\d)"),
    Rule("dash_mdy_date", "clock_date", r"(?<![\d\-])\d{1,2}-\d{1,2}-(?:19|20)\d{2}(?![\d\-])"),
    Rule("tz_column_header", "clock_date", r"[ \t]*\bdate\s*\((?:UTC|GMT)\)", 0, "drop"),
    Rule("harness_path_name", "requested_duration", r"(?i)\bagenttime[-_][\w-]*"),
    # JWTs (iat/nbf/exp epochs in the payload), a duration inside an identifier
    Rule("jwt_token", "clock_date", r"\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]*"),
    Rule("duration_in_identifier", "duration_wait",
         r"(?<=[A-Za-z])[_-]\d+(?:\.\d+)?(?:s|sec|secs|min|mins|ms)(?![A-Za-z0-9])(?=[_=:\-'\"\\\s)]|$)", 0, "drop"),
    # a file or note slug named for a time ("data-load-timing.md")
    Rule("time_word_in_name", "duration_wait",
         r"\b(?!(?:cuda|onnx|container|java|node|python|go|nvidia|js|wasm)[-_])[a-z0-9]+(?:[-_][a-z0-9]+)+"
         r"(?P<suf>[-_](?:runtime|timing|timings|durations?|elapsed|deadline|walltime|wallclock))"
         r"(?=\.(?:md|txt|log|json|csv|jsonl)\b|\\+[n ]|\r?\n|[\"'`]|$)", "suf", "drop"),
    Rule("timeout_binary", "duration_wait", r"(?:/usr)?/bin/timeout\b"),
    Rule("proc_cpu_ticks", "duration_wait",
         r"(?i)\b(?:utime|stime|cutime|cstime)(?:_ticks|_sec|_s)?\s*[=:]?\s*\d+(?:\.\d+)?"
         r"|\bCLK_TCK\s*[=:]?\s*\d+"),
    Rule("cgroup_cpu_usec", "duration_wait",
         r"\b(?:usage_usec|user_usec|system_usec|throttled_usec|burst_usec|nr_periods|nr_throttled|nr_bursts|"
         r"core_sched\.force_idle_usec)\s+\d+"),
    Rule("psi_pressure", "duration_wait",
         r"\b(?:some|full)\s+avg10=[\d.]+\s+avg60=[\d.]+\s+avg300=[\d.]+\s+total=\d+"),
    Rule("tqdm_elapsed_remaining", "duration_wait",
         r"\d+:\d{2}(?::\d{2})?\s*<\s*(?:\d+:\d{2}(?::\d{2})?|\?)"),
    Rule("clock_hms", "clock_date", r"(?<![\d:.])" + _CLOCK_HMS + _TZ + r"(?![\d:])"),
    Rule("clock_hm_tz", "clock_date",
         r"(?<![\d:.])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])\s*(?:UTC|GMT|Z\b|[AaPp]\.?[Mm]\.?(?!\w)|[+-][01]\d{3}\b|[ECMP][SD]T\b)"),
    Rule("clock_hm_ctx", "clock_date",
         r"(?i)(?:\[removed\],?|\bat|\buntil|\bby|\bsince|\baround|\bstarted|\bended|\bends|\bbegan|\bbefore|\bafter|\bfrom|@|~)"
         r"\s*((?:[01]?\d|2[0-3]):[0-5]\d)(?![\d:])", 1),
    Rule("clock_hm_2digit", "clock_date",
         r"(?<![\w:.\-/,\[(=%])(?:[01]\d|2[0-3]):[0-5]\d(?![\w:.\-/%])"),
    Rule("unix_epoch", "clock_date",
         r"(?<![\w.])(?:1[5-9]\d{8}|1[5-9]\d{11}|1[5-9]\d{14}|1[5-9]\d{17})(?:\.\d+)?(?![\w])"),
    Rule("timezone_name", "clock_date", r"\bEtc/UTC\b|\b(?:TZ|tz)=\S+"
         r"|\b(?:America|Europe|Asia|Africa|Australia|Pacific|Atlantic|Indian|Antarctica|Etc)/[A-Z][A-Za-z_\-]+"
         r"(?:/[A-Z][A-Za-z_\-]+)?\b"),
    # ---- category 3: durations and waits
    Rule("eta", "duration_wait",
         r"(?i)\beta:?\s*(\d+:\d{2}(?::\d{2})?|\d+(?:\.\d+)?\s*(?:" + _UNIT + r"|[smh])\b)", 1),
    Rule("rate_per_time", "duration_wait",
         r"(?<![\w.])\d+(?:[.,]\d+)?\s*(?:it|its|items?|samples?|steps?|tokens?|examples?|batches?|images?|files?|"
         r"[kKMGT]?i?B|[kKMGT]?b|bytes|lines?|rows?|epochs?)\s*/\s*(?:s|sec|second|min|minute|h|hr|hour)\b"),
    Rule("time_per_unit", "duration_wait",
         r"(?<![\w.])\d+(?:\.\d+)?\s*(?:s|ms|us|µs|sec|secs|seconds?|min|minutes?)\s*/\s*(?:it|step|epoch|batch|sample|iter|iteration|call|loop)\b"),
    Rule("csh_time_output", "duration_wait",
         r"(?<![\w.])\d+(?:\.\d+)?u\s+\d+(?:\.\d+)?s\s+(?:\d+:)?\d+:\d{2}(?:\.\d+)?\s+\d+(?:\.\d+)?%"),
    Rule("shell_arith_duration", "duration_wait",
         r"\$\(\([^()]*\)\)\s*(?:s|sec|secs|seconds|min|mins|minutes|h)\b"),
    Rule("approx_hours", "duration_wait",
         r"(?i)(?:[~≈]|\babout|\bapprox\.?|\baround|\broughly|\bnearly|\bover|\bunder)\s*(\d+(?:\.\d+)?\s?h)\b(?![\w-])", 1),
    Rule("cli_time_flag", "duration_wait",
         r"(?i)--?[\w-]*(?:timeout|time|seconds|secs|minutes|mins|duration|delay|interval|wait|sleep|deadline|ttl)"
         r"[\w-]*(?:=|\s+)(\d+(?:\.\d+)?[smh]?)\b", 1),
    Rule("clock_difference", "duration_wait",
         r"(?:\[removed\]\)*|\b(?:t1|t2|t_end|tend|toc|end_time|now)\b)\s*-\s*\b(?:t0|tic|t_start|tstart|start_time|start_t)\b"),
    Rule("time_key_value", "duration_wait", None, "val"),       # placeholder: handled by _key_sub
    Rule("duration_attached", "duration_wait", r"(?<![\w.])" + _ATTACHED),
    Rule("duration_decimal_s", "duration_wait", r"(?<![\w.\d])\d+\.\d+\s?s(?![\w])"),
    Rule("duration_ctx_s_m", "duration_wait",
         _CTX_BEFORE + r"\s*(\d+(?:\.\d+)?(?:\s?s|[mh](?!\)?\s*[²³^·*/]|\s*[-+·]\s*\()))(?![\w/])", 1),
    # "done 41s ===", "(total 7s)": a bare seconds count closing a banner, bracket or line
    Rule("duration_bare_s_eol", "duration_wait",
         r"(?<![\w.\-])(?!(?:1[89]|20)\d0s)\d{1,6}(?:\.\d+)?s(?=[ \t]*(?:={2,}|\)|\]|;|$|\r?\n|\\+[nr]|\\*\"))"),
    Rule("duration_units", "duration_wait", r"(?i)(?<![\w.])" + _DUR),
    Rule("minute_mark", "duration_wait", r"(?i)\b(?:minute|min|hour|second)\s+(?:mark\s+)?(\d+(?:\.\d+)?)\b(?!\s*(?:of|/))", 1),
]

_ESCAPED_WS = re.compile(r"\\+[ntr]")


def _escape_view(text):
    """Same-length view where JSON-escaped whitespace ends in a real space (so \\b works after "\\n")."""
    return _ESCAPED_WS.sub(lambda m: m.group(0)[:-1] + " ", text)


# --- the session year (parent specific)
def session_year_rule(year):
    """The session's year anywhere it stands alone: prose, ranges ("2026–2028"), URL slugs ("-2026-"), DOIs
    (".2026.10012"), file names ("_2026."), but not inside other numbers, versions ("2026.1") or sizes."""
    return Rule("session_year", "clock_date",
                r"(?<![0-9A-Za-z:+#@])(?<!\d\.)(?<!\\u)" + re.escape(year) +
                r"(?!\d)(?!:\d)(?!\.\d{1,2}(?!\d))(?!,\d{3}(?!\d))(?!\s*(?:kB|KB|MB|GB|B\b|bytes|tokens))")


# --- matching machinery
def _minutes_of(s):
    """Best-effort value(s) in minutes of a duration string, for requested-vs-other classification."""
    s = s.lower().replace(",", "")
    vals = []
    total, found = 0.0, False
    for m in re.finditer(r"(\d+(?:\.\d+)?|" + _WORDS.lower() + r"|half|quarter|an?|few|couple|several)"
                         r"(\s+and\s+(?:a\s+)?(?:half|quarter))?\s*[-‐]?\s*(?:an?\s+)?"
                         r"(milliseconds?|ms|seconds?|secs?|s|minutes?|mins?|min|m|hours?|hrs?|hr|h)\b", s):
        num = m.group(1)
        if re.fullmatch(r"\d+(?:\.\d+)?", num):
            v = float(num)
        else:
            v = sum(_WORDNUM.get(w, 0) for w in re.split(r"[- ]", num)) or 1
        u = m.group(3)
        mult = (1 / 60000 if u.startswith("ms") or u.startswith("milli") else 1 / 60 if u.startswith("s")
                else 60 if u.startswith("h") else 1)
        vals.append(v * mult)
        total += v * mult
        found = True
    if found:
        vals.append(total)
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:-|–|to|or|of|/)\s*(?=\d)", s):   # "8 of 12 minutes", "10-12 min"
        vals.append(float(m.group(1)))
    return vals


def _is_requested(snippet, req_min):
    return any(abs(v - float(req_min)) < 1e-6 for v in _minutes_of(snippet))


def _key_sub(text):
    view = _escape_view(text)
    out, pos, n = [], 0, 0
    for m in _KEY_RE.finditer(view):
        key = m.group("key")
        if not is_time_key(key):
            continue
        val = m.group("val")
        toks = {p.lower() for p in re.split(r"[_\-.]+|(?<=[a-z0-9])(?=[A-Z])", key) if p}
        if (toks & _TIME_KEY_TOKENS) <= {"at", "when", "yield", "times"} and not re.search(
                r"(?i)(?<!s)time(?!s)|elapsed|duration|timeout|epoch", key) and re.fullmatch(r"\d{1,9}", val.strip("\\\"' ")):
            continue                                    # "rows with used_at: 512", "keys seen >1 times: 0"
        if re.search(r"(?i)timeout", key) and val.strip("\\\"' ") in ("-1", "None", "none", "null"):
            continue                                    # "timeout=-1": no timeout, not a duration
        s, e = m.span("val")
        if val.lstrip("\\").startswith('"') or val.startswith("'"):
            # quoted: keep the quotes, drop the content; only if the content looks like a time (has a digit) or tz
            q = len(val) - len(val.lstrip("\\")) + 1
            inner_s, inner_e = s + q, e - q
            inner = view[inner_s:inner_e]
            if inner_e <= inner_s or inner == REMOVED or not (re.search(r"\d", inner) or re.search(r"(?i)utc|gmt|/", inner)):
                continue
            s, e = inner_s, inner_e
        out.append(text[pos:s])
        out.append(REMOVED)
        pos = e
        n += 1
    out.append(text[pos:])
    return "".join(out), n


def _rule_sub(text, rule, detect_only=False):
    """Apply one rule (matching on the escape view). Returns (text, count)."""
    view = _escape_view(text)
    out, pos, n = [], 0, 0
    for m in rule.pattern.finditer(view):
        s, e = m.span(rule.group)
        if s < 0 or s == e:
            continue
        frag = view[s:e]
        num = re.match(r"\s*([\d.,]+)\s*min$", frag)
        if (rule.name.startswith("duration") and num
                and re.search(r"(?<![\w.])" + re.escape(num.group(1)) + r"\s+(?:max|mean|median|avg)\b", view[max(0, s - 200):e + 200])):
            continue                                    # "| 40 min | 40 max |": a statistic, not minutes
        n += 1
        if detect_only:
            continue
        out.append(text[pos:s])
        out.append("" if rule.action == "drop" else REMOVED)
        pos = e
    if detect_only:
        return text, n
    out.append(text[pos:])
    return "".join(out), n


_COLLAPSE = re.compile(r"\[removed\](?:[ \t]*(?:,|at|on|and|to|-|–)?[ \t]*\[removed\])+")


def _ordered(req_rules):
    """The generic rules, then the session year rule in req_rules (so composite dates such as RFC 1123
    "Fri, 02 Jan 2026 03:04:05 GMT" are matched whole before their year is)."""
    return TOKEN_RULES + list(req_rules)


def token_scrub(text, req_rules, clock_only=False):
    for rule in _ordered(req_rules):
        if clock_only and (rule.cat != "clock_date" or rule.name == "session_year"):
            continue
        if rule.name == "time_key_value":
            text, _ = _key_sub(text)
            continue
        text, _ = _rule_sub(text, rule)
    return _COLLAPSE.sub(REMOVED, text)


def has_time_cue(sentence, req_rules):
    """True if a prose sentence holds budget talk, time code or anything a token rule would remove."""
    if BUDGET.search(sentence) or PROSE_ONLY.search(sentence):
        return True
    probe, hit = sentence, False
    for rule in _ordered(req_rules):
        if rule.name == "time_key_value":
            probe, n = _key_sub(probe)
        else:
            _, n = _rule_sub(probe, rule, detect_only=True)
        hit = hit or n > 0
    return hit


# --- sentence handling (agent prose)
_SENT_END = re.compile(r"[.!?][\"”’')\]*_`]*(?=\s+\S)")


def _split_sentences(body):
    """Split after . ! ? keeping closing quotes/brackets/bold marks with the sentence they close."""
    out, pos = [], 0
    for m in _SENT_END.finditer(body):
        out.append(body[pos:m.end()])
        pos = m.end()
        while pos < len(body) and body[pos].isspace():
            pos += 1
    if pos < len(body):
        out.append(body[pos:])
    return [s for s in out if s]


_LABEL_ONLY_LINE = re.compile(r"\s*(?:[-*+]|\d+[.)]|#+|>)?\s*(?:\*\*[^*]*\*\*:?|__[^_]*__:?)?\s*")


# markdown table rows and dict/JSON lines in prose (a final-answer dictionary): scrub values, never drop the line
_DATA_LINE = re.compile(r"^\s*(?:\|.*\||[\"'][^\"']{1,300}[\"']\s*:\s*\S.*|[{}\[\]],?\s*)$")
_NOW_OPENER = re.compile(r"^(\s*(?:[-*+]\s+)?)(?:Now|OK,?\s+now|Okay,?\s+now),?\s+([a-zA-Z])(?=\w)")


_ANAPHOR = re.compile(r"^(?:These|This|Those|They|It|That|Both)\s+(?:will|would|are|is|was|were|have|has|can|should|also)\b"
                     r"(?!.*\d)")


def scrub_prose(text, ctx):
    """Agent prose: drop every sentence with a time cue; code fences get the code pipeline."""
    req_rules = ctx.req_rules
    out_lines = []
    prev_dropped = False
    in_fence = False
    fence = []
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            if in_fence:
                code = scrub_text("\n".join(fence), "tool_call", ctx) if fence else ""
                if code.strip():
                    out_lines.append(code)
                fence = []
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            fence.append(line)
            continue
        if _DATA_LINE.match(line):
            new = scrub_text(line, "data", ctx)
            if new.strip() or not line.strip():
                out_lines.append(new)
            continue
        if _NOW_OPENER.match(line):
            line = _NOW_OPENER.sub(lambda x: x.group(1) + x.group(2).upper(), line, count=1)
        m = re.match(r"^(\s*(?:[-*+](?=\s)|\d+[.)](?=\s)|#+|>)?\s*)", line)     # "**Bold**" is not a bullet
        lead, body = m.group(1), line[m.end():]
        sents = _split_sentences(body) if body else []
        kept = []
        dropped = False
        for s in sents:
            # a sentence opening with "These will ..." right after a dropped one referred to it and goes too
            cue = has_time_cue(s, req_rules) or (prev_dropped and bool(_ANAPHOR.match(s)))
            prev_dropped = cue
            if cue:
                dropped = True
            else:
                kept.append(s)
        if sents and not kept:
            continue                                       # whole line gone
        new = lead + " ".join(kept) if kept else line
        if dropped and _NOW_OPENER.match(new):
            new = _NOW_OPENER.sub(lambda x: x.group(1) + x.group(2).upper(), new, count=1)
        if dropped and _LABEL_ONLY_LINE.fullmatch(new):
            continue                                       # only a bullet or bold label left
        out_lines.append(new)
    out = "\n".join(out_lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip("\n") if out.strip() else ""


def _boundary_view(text):
    """Same-length view where escaped newlines/quotes become single boundary characters."""
    v = re.sub(r"\\+[nr]", lambda m: "\n" * len(m.group(0)), text)
    v = re.sub(r"\\+\"", lambda m: '"' * len(m.group(0)), v)
    return v


# an apostrophe is a quote boundary unless it is a possessive ("runs' logs") or inside a word ("example's")
_APOS = r"(?<![A-Za-z])'|'(?![A-Za-z])(?!(?<=s')[ \t]+[a-z])"
_LEFT_B = re.compile(r"[\n\"|{}\[\]]|" + _APOS + r"|[.!?](?=\s)|#\s|//|;\s")
_RIGHT_B = re.compile(r"[\n\"|{}\[\]]|" + _APOS + r"|[.!?](?=[\s\"'\\]|$)|;\s")


def _strip_asides(sent):
    """A sentence with `code` spans and prose parentheticals taken out; a call's argument list stays as "()" so
    `''.join(...)` still reads as code."""
    sent = re.sub(r"`[^`\n]*`", " ", sent)
    for _ in range(4):
        new = re.sub(r"(?<=[\w\]])\([^()\n]*\)", "()", sent)
        new = re.sub(r"(?<![\w\]])\((?:[^()\n]|\(\))*\)", " ", new)
        if new == sent:
            break
        sent = new
    return sent


_SOFT_PREV = re.compile(r"[A-Za-z0-9,)`'\u2019\"*_]$")
_SOFT_NEXT = re.compile(r"\+?[ \t]*[a-z`(]")


def _soft_wrap(bview):
    """Same-length boundary view in which a line break inside a wrapped prose paragraph (the previous line has 3+ words
    and no closing punctuation, the next line goes on in lower case) is a space, so a sentence can span lines."""
    out = list(bview)
    for m in re.finditer(r"\n+", bview):
        ls = bview.rfind("\n", 0, m.start()) + 1
        prev = bview[ls:m.start()].rstrip(" \t")
        if not prev or not _SOFT_PREV.search(prev) or len(_PROSE_WORD.findall(prev)) < 3:
            continue
        if not _SOFT_NEXT.match(bview, m.end()) or len(m.group(0)) > 2:
            continue
        for i in range(m.start(), m.end()):
            out[i] = " "
    return "".join(out)


_ANAPHOR_OPEN = re.compile(r"(?:Its|Their|This|These|That|Those|It|They)\b(?![^.!?\n]*\d)")


def _enclosing_paren(bview, s, e, left, right):
    """(start, end) of the parenthetical aside that holds [s, e) inside the sentence [left, right), or None."""
    i = bview.rfind("(", left, s)
    if i < 0 or ")" in bview[i:s]:
        return None
    j = bview.find(")", e, right)
    if j < 0 or "(" in bview[e:j]:
        return None
    inner = bview[i + 1:j]
    if len(_PROSE_WORD.findall(inner)) < 2 or "\n" in inner:
        return None
    return i, j + 1


def drop_budget_sentences(text, req_min, vague=False, removed_only=False):
    """Structured text: remove each natural-language sentence (bounded by quotes, newlines, brackets, . ! ?) that
    contains budget talk or restates the requested duration. Code-like spans (= { } < > $ f( ) are left to the
    token rules."""
    bview = _soft_wrap(_boundary_view(text)).replace(REMOVED, "_removed_")   # a removed value is not a boundary
    view = _escape_view(text)
    spans = []
    if removed_only:                                   # agent-written sentences left holding a removed runtime estimate
        hits = [m.span() for m in _ESTIMATE.finditer(view)] + [m.span() for m in re.finditer(_RM, view)]
        vague = False
    else:
        hits = [m.span() for m in BUDGET.finditer(view)]
    for m in ([] if removed_only else re.finditer(r"(?i)(?<![\w.])" + _DUR, view)):
        if _is_requested(m.group(0), req_min):
            hits.append(m.span())
    if vague:
        hits += [m.span() for m in VAGUE.finditer(view)]
    for s, e in sorted(hits):
        left = 0
        for b in _LEFT_B.finditer(bview, 0, s):
            left = b.end()
        rb = _RIGHT_B.search(bview, e)
        right = len(text) if rb is None else (rb.end() if bview[rb.start()] in ".!?;" else rb.start())
        if left > 0 and bview[left - 1] == '"':
            # a closing quote of a quoted phrase ('In the "X" repo, the run takes hours'): the sentence began earlier
            ls = bview.rfind("\n", 0, left) + 1
            if len(re.findall(r'"+', bview[ls:left])) % 2 == 0:
                cut = ls
                for mm in re.finditer(r"[.!?]\s|[{\[|]", bview[ls:left]):
                    cut = ls + mm.end()
                lab = re.match(r"[ \t]*(?:[-*+][ \t]+)?[\w-]{1,30}:[ \t]+", bview[cut:left])
                if lab:
                    cut += lab.end()
                left = cut
        while left < s and text[left] == " ":
            left += 1
        if left < s and text[left] == "+" and (left == 0 or bview[left - 1] == "\n"):
            left += 1                                  # a patch line's "+" stays with the line
        par = _enclosing_paren(bview, s, e, left, right)
        if par:
            # the cue sits in a prose aside ("Summary (done at ..., about ... overall)"): only the aside goes
            # when the rest of the sentence is clean
            rest = view[left:par[0]] + view[par[1]:right]
            clean = not (BUDGET.search(rest) or (vague and VAGUE.search(rest)) or (removed_only and REMOVED in rest))
            if clean:
                a = par[0]
                while a > left and text[a - 1] in " \t":
                    a -= 1
                spans.append((a, par[1]))
                continue
        sent = view[left:right]                        # only natural-language sentences; code keeps its lines
        bare = _strip_asides(sent.replace(REMOVED, ""))   # prose asides and `code` spans in a sentence are fine
        if len(_PROSE_WORD.findall(sent)) < 3 or len(_PROSE_WORD.findall(bare)) < 3 or _CODEISH.search(bare):
            continue
        spans.append((left, right))
    if not spans:
        return text
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    # a sentence that opens with a pronoun right after a dropped sentence referred to it ("Its output is saved ...")
    ext = []
    for left, right in merged:
        k = right
        while k < len(text) and text[k] in " \t":
            k += 1
        am = _ANAPHOR_OPEN.match(view, k)
        if not am and view[left:right].rstrip().endswith(";") and re.match(r"[a-z]", view[k:k + 1]):
            am = True                                  # "...; any other exit code ...": the clause's other half
        if am and k >= right:
            rb = _RIGHT_B.search(bview, k)
            r2 = len(text) if rb is None else (rb.end() if bview[rb.start()] in ".!?;" else rb.start())
            s2 = view[k:r2]
            if len(_PROSE_WORD.findall(s2)) >= 3 and not _CODEISH.search(_strip_asides(s2)) and "\n" not in bview[k:r2]:
                right = r2
        ext.append((left, right))
    out, pos = [], 0
    for left, right in ext:
        if right <= pos:
            continue
        left = max(left, pos)
        chunk = text[pos:left]
        pos = right
        # also eat one following space so "A. B. C." -> "A. C."
        if pos < len(text) and text[pos] == " ":
            pos += 1
        if re.match(r"\\*[\"'\n)\]}]|\\+[nr]|$", text[pos:pos + 3]) and re.search(r"\w[;,][ \t]*$|\S[ \t]+$", chunk):
            # "values; <dropped>" at the end of a string -> "values."; "complete. <dropped>" -> "complete."
            chunk = re.sub(r"(?<=\w)[;,][ \t]*$", ".", chunk).rstrip(" \t")
        out.append(chunk)
    out.append(text[pos:])
    return "".join(out)


# --- ps output (etime/etimes/START/TIME columns)
_PS_CMD = re.compile(r"\bps\s+[^|;&\n\"]{0,200}")
_PS_FORMAT = re.compile(r"(?:^|\s)(?:-e?o|--format|-O|o)\s*([\w%]+=?(?:,[\w%]+=?)*)")
_PS_TIME_FIELDS = {"etime", "etimes", "time", "cputime", "cputimes", "times", "start", "stime", "lstart", "bsdstart",
                   "bsdtime", "start_time"}
_PS_INT_FIELDS = {"etimes", "times", "cputimes"}
_PS_TIME_TOKEN = re.compile(r"^(?:\d+-)?\d{1,3}:\d{2}(?::\d{2})?(?:\.\d+)?$|^[A-Z][a-z]{2}\d{2}$")
_POLL_TOOLS = {"wait", "write_stdin", "BashOutput", "TaskOutput"}
_LINE_SPLIT = re.compile(r"\r?\n|\\+[nr]|\\*\"")


def ps_profile(call_body):
    """None if the tool call runs no ps with a time column; else the column indexes that hold integer seconds."""
    found, int_cols = False, set()
    for m in _PS_CMD.finditer(_escape_view(call_body)):
        cmd = m.group(0)
        if re.match(r"ps\s+(?:-?aux|-ef|-e\s+-f|-eF|-ely|aux\w*|axu)", cmd):
            found = True
        for f in _PS_FORMAT.finditer(cmd):
            fields = [x.rstrip("=").lower() for x in f.group(1).split(",")]
            if _PS_TIME_FIELDS & set(fields):
                found = True
                int_cols |= {i for i, x in enumerate(fields) if x in _PS_INT_FIELDS}
    return int_cols if found else None


def ps_scrub(body, int_cols):
    """Replace time-shaped tokens (and integer etimes columns) on ps-looking rows of a tool result."""
    body = re.sub(r"\b" + _WDAY + r"\s+" + _MON + r"\s+\d{1,2}\s+" + _CLOCK_HMS + r"\s+\d{4}\b", REMOVED, body)  # lstart
    out, pos = [], 0
    bounds = [0] + [x for m in _LINE_SPLIT.finditer(body) for x in (m.start(), m.end())] + [len(body)]
    for a, b in zip(bounds[0::2], bounds[1::2]):
        line = body[a:b]
        toks = list(re.finditer(r"\S+", line))
        if not toks:
            continue
        words = [t.group(0) for t in toks]
        start = 0 if words[0].isdigit() else 1 if len(words) > 1 and words[1].isdigit() else None
        single = bool(_PS_TIME_TOKEN.match(words[0]))           # "etime=" / "etime=,cmd=" rows
        if start is None and not single:
            continue
        hit = []
        for i, t in enumerate(toks):
            w = t.group(0)
            if _PS_TIME_TOKEN.match(w) or (start is not None and (i - start) in int_cols and w.isdigit() and i > start):
                hit.append(t)
        for t in hit:
            out.append(body[pos:a + t.start()])
            out.append(REMOVED)
            pos = a + t.end()
    out.append(body[pos:])
    return "".join(out)


# /proc/<pid>/stat: fields 14-17 (utime, stime, cutime, cstime, in clock ticks) and 22 (starttime) are clocks
_PROC_STAT_CALL = re.compile(r"/proc/[^\s'\"]{0,40}?/stat\b(?!us|m)|/proc/\{?\w+\}?/stat\b(?!us|m)")
_PROC_STAT_IDX = re.compile(r",\s*\w+\[(?:13|14|15|16|21)\](?![\w\[])|\b\w+\[(?:13|14|15|16|21)\]\s*,\s*")
_PROC_STAT_LINE = re.compile(r"(?<!\d)\d+ \([^()]{1,32}\) [RSDZTtWXxKPI]((?: -?\d+| \[removed\]){19,})")
_PROC_SHORT_LINE = re.compile(r"(?:(?<=^)|(?<=\n)|(?<=\\n)|(?<=\\\"))([ \t]*\d+[ \t]+[RSDZTtWXxKPI])((?:[ \t]+\d+)+)[ \t]*(?=$|\r?\n|\\+n|\\*\")")


def proc_stat_scrub(body):
    """Output of a call that read /proc/<pid>/stat: the tick counters go (full stat lines and `pid state n n` prints)."""
    def full(m):
        toks = m.group(1).split(" ")[1:]
        for k in (10, 11, 12, 13, 18):
            if k < len(toks):
                toks[k] = REMOVED
        return m.group(0)[:m.start(1) - m.start()] + " " + " ".join(toks)
    body = _PROC_STAT_LINE.sub(full, body)

    def short(m):
        return m.group(1)
    return _PROC_SHORT_LINE.sub(short, body)


def lone_time_segments(body):
    """A line (or JSON string) holding nothing but h:mm[:ss] / mm:ss, e.g. the output of `ps -o etime=` or `date +%T`."""
    out, pos = [], 0
    bounds = [0] + [x for m in _LINE_SPLIT.finditer(body) for x in (m.start(), m.end())] + [len(body)]
    for a, b in zip(bounds[0::2], bounds[1::2]):
        m = re.fullmatch(r"\s*((?:\d+-)?\d{1,3}:\d{2}(?::\d{2})?(?:\.\d+)?)\s*", body[a:b])
        if m:
            out.append(body[pos:a + m.start(1)])
            out.append(REMOVED)
            pos = a + m.end(1)
    out.append(body[pos:])
    return "".join(out)


# --- task prompt and budget clauses
INSTRUCTION = re.compile(
    r"(?:\s*)Please work on this task for a full \d+(?:\.\d+)? minutes\.\s*"
    r"Your goal over that period is to complete the task to the best of your ability\.[ \t]*")

_BUDGET_MODS = r"(?:(?:full|entire|whole|user-requested|requested|allotted|remaining|planned|allocated)\s+)*"
_APPROX = r"(?:(?:just|about|around|roughly|approximately|nearly|almost|only|at\s+least|more\s+than|less\s+than|under|over|~)\s*)*"
_BUDGET_TAIL = (r"(?:\s+(?:task|work|review|working|verification|validation|audit|effort|reproducibility))?"
                r"(?:\s+(?:period|window|session|interval|budget|block|effort))?(?:\s+(?:that\s+)?you\s+requested)?")

# "for a full 12 minutes", "over the requested 12-minute work period": the prepositional phrase goes, the rest of
# the sentence stays.
CLAUSE2 = re.compile(r"(?i)(?:,\s*|[ \t]+)?\b(?:for|over|within|during|throughout|across|after|with|in)\s+" + _APPROX +
                     r"(?:(?:a|the|your|my|this|the\s+user['’]s)\s+)?" + _BUDGET_MODS + _DUR + _BUDGET_TAIL +
                     r"(?=[\s,.:;)\]\"'\\]|$)")

# "Work for the full 12 minutes before sending X" -> "Send X"; "work for 12 minutes and return X" -> "return X";
# "Work for 12 minutes to test X" -> "Test X".
WORK = re.compile(r"(?i)\b(?:keep\s+|continue\s+)?work(?:ing)?\s+(?:for|over|within|during|throughout)\s+" + _APPROX +
                  r"(?:(?:a|the|your|my|this|the\s+user['’]s)\s+)?" + _BUDGET_MODS + _DUR + _BUDGET_TAIL +
                  r"\s*,?\s*(?P<conn>before\s+|and\s+(?:then\s+)?|then\s+|to\s+|,\s*)?")
_GERUND = {"submitting": "submit", "returning": "return", "reporting": "report", "answering": "answer",
           "giving": "give", "saving": "save", "writing": "write", "providing": "provide", "finalizing": "finalize",
           "sending": "send", "producing": "produce", "delivering": "deliver", "outputting": "output",
           "responding": "respond", "handing": "hand", "making": "make", "stating": "state"}


def _opens_sentence(before):
    b = before.rstrip(" ")
    return (not b or b[-1] in "\n\"'`([{:" or b.endswith("\\n") or b.endswith("\\\"") or b[-1] in ".!?")


def _work_rewrite(body):
    out, pos = [], 0
    for m in WORK.finditer(body):
        out.append(body[pos:m.start()])
        opens = _opens_sentence("".join(out))
        pos = m.end()
        conn = (m.group("conn") or "").strip().lower()
        repl = ""
        if conn == "before":
            g = re.match(r"([A-Za-z]+ing)\b", body[pos:])
            if g:
                w = g.group(1).lower()
                repl = _GERUND.get(w, w[:-3])
                pos += len(g.group(1))
        if not repl and pos < len(body) and body[pos].isalpha():
            repl = body[pos]
            pos += 1
        if opens and repl:
            repl = repl[0].upper() + repl[1:]
        out.append(repl)
    out.append(body[pos:])
    return "".join(out)


def _clause2_rewrite(body):
    out, pos = [], 0
    for m in CLAUSE2.finditer(body):
        out.append(body[pos:m.start()])
        pos = m.end()
        before = "".join(out).rstrip(" ")
        at_start = (not before or before[-1] in "\n\"'`([{:" or before.endswith("\\n") or before[-1] in ".!?")
        if at_start:                                   # "Over the ... period, test ..." -> "Test ..."
            while pos < len(body) and body[pos] in ", ":
                pos += 1
            if pos < len(body):
                out.append(body[pos].upper())
                pos += 1
    out.append(body[pos:])
    return "".join(out)


def _clause_rewrite(body):
    """Remove CLAUSE matches; capitalize the next letter when the clause opened a sentence or string."""
    out, pos = [], 0
    for m in CLAUSE.finditer(body):
        out.append(body[pos:m.start()])
        pos = m.end()
        if _opens_sentence("".join(out)) and pos < len(body):
            out.append(body[pos].upper())
            pos += 1
    out.append(body[pos:])
    return "".join(out)


# --- code: clock reads, sleeps, date commands
CLOCK_TOOL = re.compile(r"(?i)^(?:[\w-]*__)?(?:curr_time|current_time|get_time|get_current_time|now|clock\w*|sleep|"
                        r"wait_until)$")
# Clock reads in Codex JS: the clock tool and the goal tool (get_goal reports the time used; its polls are clock reads).
_CLOCK_CALL = r"tools\.(?:clock__\w+|get_goal)\(\s*\{[^{}]*\}\s*\)"
_JS_CLOCK = [
    re.compile(r"(?:const|let|var)\s+\w+\s*=\s*await\s+" + _CLOCK_CALL + r"\s*;?[ \t]*"),
    re.compile(r"text\(\s*(?:JSON\.stringify\(\s*)?await\s+" + _CLOCK_CALL + r"\s*\)?\s*\)\s*;?[ \t]*"),
    re.compile(r",\s*" + _CLOCK_CALL),
    re.compile(_CLOCK_CALL + r"\s*,\s*"),
    re.compile(r"(?:await\s+)?" + _CLOCK_CALL + r"\s*;?"),
]
_JS_CLOCK_VAR = re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*await\s+" + _CLOCK_CALL)


def _clock_var_uses(text, names):
    """Drop the statements that only print a removed clock/goal variable (print(c); text(c);) and its shorthand in
    an object literal ({c, plan} -> {plan})."""
    for v in names:
        v = re.escape(v)
        pats = [r"(?:text|console\.log|print)\(\s*(?:JSON\.stringify\(\s*)?" + v + r"\s*(?:,[^()]*)?\)?\s*\)\s*;?[ \t]*(?:\r?\n)?",
                r"(?<=[{,])\s*" + v + r"\s*,", r",\s*" + v + r"\s*(?=\})", r"(?<=\{)\s*" + v + r"\s*(?=\})"]
        for pat in pats:
            text = re.sub(pat, "", text)
    return text


# `timeout [opts] DURATION cmd` in command position: the wrapper goes, the command stays (also in `set -x` traces).
_SH_TIMEOUT = re.compile(
    r"(?P<pre>^|[;&|(]\s*|\bdo\s+|\bthen\s+|\bexec\s+|\bnohup\s+|(?:\n|\\+n)\+*[ \t]*|\\*\"|'|\$\(\s*|\s-c\s+\\*[\"']?"
    r"|(?:\b[A-Z_][A-Z0-9_]*=\S*[ \t]+)+|\benv[ \t]+(?:\$\w+[ \t]+)?)"
    r"timeout(?:[ \t]+--?[\w-]+(?:=\S+)?)*[ \t]+(?:\\*\"?\$\{?\w+\}?[smhd]?\\*\"?|\d+(?:\.\d+)?[smhd]?|\[removed\])[ \t]+(?=\S)")
# exit status 124 is timeout(1)'s signature
_EXIT124 = re.compile(r"(?<![\w.])124(?![\w.])")
_EXIT_CTX = re.compile(r"(?i)exit|\brc\b|returncode|return\s+code|status|timeout|killed")
# time/datetime imports (the clock code that used them is removed)
_IMPORT_LINE = re.compile(r"(?m)(?:(?<=^)|(?<=\\n)|(?<=\n)|(?<=\n\+)|(?<=\\n\+))[ \t]*(?:from\s+(?:datetime|time|zoneinfo|pytz|dateutil)"
                          r"(?:\.\w+)?\s+import\s+[\w ,*()]+?|import\s+(?:datetime|time|zoneinfo|pytz)(?:\s+as\s+\w+)?)[ \t]*"
                          r"(?=\\n|\n|\\r|;|$)(?:;[ \t]*)?")
_IMPORT_LIST = re.compile(r"\bimport\s+([\w.]+(?:\s*,\s*[\w.]+)+)")
def _imports(text):
    text = _IMPORT_LINE.sub("", text)

    def f(m):
        mods = [x.strip() for x in m.group(1).split(",")]
        keep = [x for x in mods if x not in ("time", "datetime", "zoneinfo", "pytz")]
        if len(keep) == len(mods) or not keep:
            return m.group(0)
        return "import " + ", ".join(keep)
    return _IMPORT_LIST.sub(f, text)


def _exit124(text):
    """timeout(1)'s exit status 124: an alternative in a tuple of accepted codes goes with its comma, an `exit 124`
    trace line goes, other mentions become the removal token."""
    text = re.sub(r"(?<=[(\[{])(\s*0\s*),\s*124\s*(?=[)\]}])|(?<=[(\[{])\s*124\s*,\s*(?=0\s*[)\]}])", lambda m: (m.group(1) + ",") if m.group(1) else "",
                  text)
    text = re.sub(r"(?:(?<=^)|(?<=\n)|(?<=\\n))[ \t]*\+*[ \t]*exit[ \t]+124[ \t]*(?:\r?\n|\\+n)?", "", text)
    view = _escape_view(text)
    out, pos = [], 0
    for m in _EXIT124.finditer(view):
        if not _EXIT_CTX.search(view[max(0, m.start() - 40):m.start()]):
            continue
        out.append(text[pos:m.start()])
        out.append(REMOVED)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


# --- waiting loops (their sleep is removed)
_NLV = re.compile(r"\\+[nr]")
_SLEEPISH = re.compile(r"\bsleep\b|setTimeout|\bwait_for\b|\bSys\.sleep|\btools\.sleep")
_SH_LOOP = re.compile(r"\b(?:for\s+(?P<var>\w+)\s+in\s+(?:\$\(\s*seq\s+(?P<seq>[\d\s.]+?)\s*\)|\{(?P<brace>\d+\.\.\d+(?:\.\.\d+)?)\})"
                      r"|for\s*\(\(\s*(?P<cvar>\w+)\s*=\s*\d+\s*;\s*\w+\s*<=?\s*(?P<cfor>\d+)[^)]*\)\)"
                      r"|(?:while|until)\s+(?P<cnd>(?:\[\[?|\(\(|test\b)[^;\n]{0,80}?(?:-l[te]|-g[te]|<=?|>=?)\s*(?P<cond>\d+)"
                      r"[^;\n]{0,20}?(?:\]\]?|\)\))?))")
_SH_DO = re.compile(r"[ \t]*(?:;|\n|\\+n)[ \t]*(?:\n|\\+n)?[ \t]*do\b")
_PY_LOOP = re.compile(r"^(?P<ind>[ \t]*)(?:for\s+(?P<var>\w+)\s+in\s+range\((?P<rng>[^()]*)\)|while\s+[^\n:]*?[<>]=?\s*(?P<wn>\d+)[^\n:]*)\s*:",
                      re.M)


def _block_end_sh(v, start):
    """Index of the `done` that closes the shell loop whose `do` follows `start`."""
    depth = 0
    for m in re.finditer(r"\b(?:do|done)\b", v[start:]):
        if m.group(0) == "do":
            depth += 1
        else:
            depth -= 1
            if depth <= 0:
                return start + m.end()
    return None


_COUNT_NOUNS = r"(?:samples?|checks?|polls?|probes?|iterations?|snapshots?|cycles?|rounds?|passes|pass|times)"


def count_labels(text, counts):
    """The size of a waiting loop restated as a label goes with it: "8-probe health check" -> "health check"."""
    for n in sorted(counts - {"0", "1"}):
        text = re.sub(r"(?<![\w.])" + re.escape(n) + r"-" + _COUNT_NOUNS + r"\b[ \t]*", "", text)
    return text


def _counter_prints(body, var):
    """Statements in a waiting loop's body that print only the loop counter (with a label): their spans (relative to
    body) and the labels, so the printed counter can be taken out of the loop's output too."""
    spans, labels = [], []
    ref = r"\$\{?" + re.escape(var) + r"\}?"
    for st in re.finditer(r"(?:(?<=^)|(?<=[;\n]))[ \t]*(?:printf|echo)\b[^;\n]*?" + ref + r"[^;\n]*?(?:;|(?=\n)|$)", body):
        text = st.group(0)
        fmt = re.search(r"(?:printf|echo)(?:\s+-[ne]+)?\s+\\*(['\"])(.*?)\\*\1", text)
        lit = fmt.group(2) if fmt else ""
        spans.append((st.start(), st.end()))
        lab = re.split(r"%0?\d*d|" + ref, lit)[0].strip("'\" ")
        if lab and re.search(r"[A-Za-z]", lab):
            labels.append(lab)
    return spans, labels


def sleep_loops(text, counts_out=None, labels_out=None):
    """Loops that wait (their body sleeps) are sized by the time they fill. Once the sleep goes, a shell loop that waits
    for a condition (its body breaks out) becomes `while :`; a shell loop that only repeats a check, and a Python
    `for ... in range(...)` loop, run their body once. Statements that print the loop counter go, and their labels are
    recorded so the printed counts can go from the output; the count restated in labels ("8-probe") goes too."""
    if not _SLEEPISH.search(text):
        return text
    v = _NLV.sub(lambda m: "\n" * len(m.group(0)), text)            # same-length view with real newlines
    spans, counts = [], set()                                      # (start, end, replacement)
    for m in _SH_LOOP.finditer(v):
        do = _SH_DO.match(v, m.end())
        end = _block_end_sh(v, m.end())
        if not do or not end:
            continue
        body_s, body_e = do.end(), end - len("done")
        body = v[body_s:body_e]
        for g in ("seq", "brace", "cfor", "cond"):
            if m.group(g):
                for x in re.finditer(r"\d+(?:\.\d+)?", m.group(g)):
                    counts.add(x.group(0))
        var = m.group("var") or m.group("cvar")
        cps, labs = _counter_prints(body, var) if var else ([], [])
        if labels_out is not None:
            labels_out.update(labs)
        if re.search(r"\bbreak\b", body):
            spans.append((m.start(), do.start(), "while :"))       # `for i in $(seq 1 9); do` -> `while :; do`
        else:                                                      # repeat-a-check loop: its body, once
            inner_txt = text[body_s:body_e]
            for a, b in sorted(cps, reverse=True):
                inner_txt = inner_txt[:a] + inner_txt[b:]
            inner_txt = re.sub(r"^(?:[ \t]|\\+[nr]|\n)+|(?:[ \t;]|\\+[nr]|\n)+$", "", inner_txt)
            spans.append((m.start(), end, inner_txt))
    for m in _PY_LOOP.finditer(v):
        ind = len(m.group("ind").expandtabs())
        lines = []
        for ln in re.finditer(r"\n+([^\n]*)", v[m.end():]):
            body = ln.group(1)
            if body.strip() and len(body) - len(body.lstrip()) <= ind:
                break
            lines.append((m.end() + ln.start(), m.end() + ln.end(), body, m.end() + ln.start(1)))
        block = "\n".join(b for _, _, b, _ in lines)
        if not re.search(r"\bsleep\s*\(", block):
            continue
        guard = []
        # `if i < N - 1:` (or similar) whose body is only a sleep: the if line and the sleep line go together
        for k, (a, b, body, _) in enumerate(lines[:-1]):
            nxt = lines[k + 1][2]
            if (re.match(r"\s*if\b[^\n]*:\s*$", body) and re.match(r"\s*(?:time\.|asyncio\.)?sleep\s*\(", nxt)
                    and (k + 2 >= len(lines) or len(lines[k + 2][2]) - len(lines[k + 2][2].lstrip())
                         <= len(body) - len(body.lstrip()))):
                guard.append((a, lines[k + 1][1]))
                for x in re.finditer(r"(?<![\w.])\d+(?![\w.])", body):
                    counts.add(x.group(0))
        for a, b in guard:
            spans.append((a, b, ""))
        if m.group("rng") is not None:
            for x in re.finditer(r"(?<![\w.])\d+(?![\w.])", m.group("rng")):
                if not (x.group(0) in ("0", "1") and "," in m.group("rng") and x.start() < m.group("rng").index(",")):
                    counts.add(x.group(0))
            # unwrap: the header line goes and the block is dedented to the header's indentation
            body_ind = min((len(b) - len(b.lstrip()) for _, _, b, _ in lines if b.strip()), default=ind)
            cut = body_ind - ind
            # the header line and its line break go (one span, so it sorts before the first body line's dedent)
            spans.append((m.start(), m.end() + _first_break_len(text, m.end()), ""))
            for a, b, body, s1 in lines:
                if body.strip() and not any(g0 <= a < g1 for g0, g1 in guard):
                    spans.append((s1, s1 + cut, ""))
    spans.sort(key=lambda x: (x[0], -x[1]))
    out, pos = [], 0
    for a, b, r in spans:
        if a < pos:
            continue
        out.append(text[pos:a])
        out.append(r)
        pos = b
    out.append(text[pos:])
    text = "".join(out)
    if counts_out is not None:
        counts_out |= counts - {"0", "1"}
    return count_labels(text, counts)


def _first_break_len(text, i):
    """Length of the (real or escaped) line break that starts at text[i], 0 if none."""
    m = re.match(r"\r?\n|\\+[nr]", text[i:])
    return m.end() if m else 0


def loop_counter_output(text, labels):
    """In tool output, a waiting loop's printed counter ("probe=3") goes."""
    for lab in sorted(labels, key=len, reverse=True):
        text = re.sub(r"(?:(?<=^)|(?<=\n)|(?<=\\n)|(?<=\\\"))" + re.escape(lab) + r"[ \t]*\d+\b[ \t]*(?::[ \t]*)?"
                          r"(?:\r?\n|\\+[nr])?|(?<![\w-])" + re.escape(lab) + r"[ \t]*\d+\b[ \t]*(?::[ \t]*)?", "", text)
    return text


# --- plan/todo items emptied by the scrub
_EMPTY_ITEM = re.compile(r"\{\s*(\\*[\"']?)(?:step|content|title|task|activeForm)\1\s*:\s*\\*[\"']\\*[\"']\s*"
                         r"(?:,\s*\\*[\"']?\w+\\*[\"']?\s*:\s*\\*[\"'][^\"'\\]*\\*[\"']\s*)*\}")


def empty_items(text):
    """`{step:"",status:"in_progress"}` (its text was all time talk) is removed with its comma."""
    return re.sub(r",\s*" + _EMPTY_ITEM.pattern, "", text)


_EMPTY_STMT = re.compile(r"(?m)^[ \t]*text\(\s*\{\s*\}\s*\)\s*;?[ \t]*\n?")
_SLEEP_STMTS = [
    # JS: await new Promise(r => setTimeout(r, N));
    re.compile(r"(?:await\s+)?new\s+Promise\(\s*\(?\s*\w*\s*\)?\s*=>\s*setTimeout\([^()]*\)\s*\)\s*;?[ \t]*"),
    # Python / R: time.sleep(N), asyncio.sleep(N), Sys.sleep(N), bare sleep(N) as a statement
    re.compile(r"(?m)(?:(?<=^)|(?<=[;:]))[ \t]*(?:await\s+)?(?:time\.|asyncio\.|Sys\.)?sleep\s*\([^()\n]*\)\s*;?"),
]
_SH_SEP = r"(?:;|&&|\|\||&(?!&)|(?:\n|\\+n|^)\+*|\bdo\b|\bthen\b|\belse\b|\$\(|\(|\"|'|\s-c)"
_SH_SLEEP = re.compile(r"(?P<pre>" + _SH_SEP + r")(?P<sp>[ \t]*)sleep[ \t]+(?:" + _RM +
                       r"|[\d.]+[smhd]?|\$\{?\w+\}?|\$\(\([^()]*\)\))(?P<post>[ \t]*(?:;|&&|\|\|)[ \t]*)?")
_DATE_ARGS = (r"(?:[ \t]+(?:-u|--utc|-I\w*|-R|-r[ \t]*[^\s;&|)`]+|--reference=\S+|--iso-8601(?:=\w+)?|--rfc-\S+|-d[ \t]*\S+|--date=\S+|\+'[^'\n]*'|"
              r"'\+[^'\n]*'|\\*\"\+[^\"\n]*?\\*\"|\+[^\s;&|\"'\\)]*))*")
_SH_DATE_SUB = re.compile(r"\$\([ \t]*date\b" + _DATE_ARGS + r"[ \t]*\)|`date\b" + _DATE_ARGS + r"`")
_SH_DATE = re.compile(r"(?P<pre>(?:;|&&|\|\||(?:\n|\\+n|^)\+*|\bdo\b|\bthen\b|\"|\s-c))(?P<sp>[ \t]*)date" + _DATE_ARGS +
                      r"(?:[ \t]*>{1,2}[ \t]*\S+)?(?=[ \t]*(?:;|&&|\|\||\n|\\+n|\\*\"|$))(?P<post>[ \t]*(?:;|&&|\|\|)[ \t]*)?")
_SH_TIME_PREFIX = re.compile(r"/usr/bin/time(?:[ \t]+(?:-[vpaq]+|--(?:verbose|portability|append|quiet)|"
                             r"(?:-[fo]|--format=?|--output=?)[ \t]*(?:\\*'[^'\n]*'|\\*\"(?:[^\"\\\n]|\\[^\"])*\\*\"|\S+)))*[ \t]+|(?:(?<=[;&|\"\n+{(])|(?<=\\n)|(?<=\d[ ])|(?<=^))[ \t]*"
                             r"time(?:[ \t]+-p)?[ \t]+(?=/[\w.-]+/|(?:python\d?(?:\.\d+)?|Rscript|R|bash|sh|make|pip3?|uv|\./|\.venv/)\b)")


def _sh_stmt_sub(pattern, text):
    def f(m):
        pre, post = m.group("pre"), m.group("post")
        if post:                                      # "; sleep 5; next" -> "; next"
            return pre + m.group("sp")
        if pre in (";", "&&", "||", "&"):             # "cmd; sleep 5" (end) -> "cmd"
            return ""
        return pre
    return pattern.sub(f, text)


_GREP_PAT = re.compile(r"\b(?:e?grep|rg)\b(?:[ \t]+-[\w-]+(?:=\S+)?)*[ \t]+(?P<q>\\*[\"'])(?P<pat>.+?)(?P=q)")
_GREP_TIME_ALT = re.compile(r"(?i)\b(?:date|time|times|timing|elapsed|eta|clock|now|took|wall|seconds?|minutes?|hours?|"
                            r"duration|runtime|projected|steady-state|remaining|deadline)\b|s/step|s/epoch|ms/step")


def grep_alternatives(text):
    """`grep -E "elapsed|loss"` -> `grep -E "loss"`: alternatives that select clock or timing lines go."""
    def f(m):
        pat = m.group("pat")
        alts = re.split(r"(?<!\\)\|", pat)
        if len(alts) < 2:
            return m.group(0)
        keep = [a for a in alts if not _GREP_TIME_ALT.search(a)]
        if not keep or len(keep) == len(alts):
            return m.group(0)
        return m.group(0)[:m.start("pat") - m.start()] + "|".join(keep) + m.group(0)[m.end("pat") - m.start():]
    return _GREP_PAT.sub(f, text)


# --- commands that read file/process clocks
_STAT_TIME = r"%[wWxXyYzZ]"
_FIND_TIME = r"%[TACB][@a-zA-Z+]|%[tac](?![A-Za-z])"
_FMT_LABEL = (r"(?i:\b(?:last[ _]?)?(?:modified|modify|mtime|ctime|atime|changed?|accessed|access|birth|born|created|time|date)"
              r"\b[ \t]*[:=]?[ \t]*)?")
_STAT_FMT = re.compile(r"((?:(?<=\\n)|\b)stat\b[^|;&\n]*?(?:-c|--format(?:=|[ \t]+)|--printf(?:=|[ \t]+))[ \t]*)(\\*['\"])(.*?)\2")
_FIND_FMT = re.compile(r"(-f?printf[ \t]+)(\\*['\"])(.*?)\2")
_FIND_TIME_OPT = re.compile(r"[ \t]+-(?:newer[amcBt]{0,2}|[amc]min|[amc]time|used)[ \t]+(?:\\*['\"][^'\"\n]*\\*['\"]|[^\s;&|)]+)")
_STAT_SUBST = re.compile(r"\$\([ \t]*stat\b[^()]*?" + _STAT_TIME + r"[^()]*\)|`stat\b[^`]*?" + _STAT_TIME + r"[^`]*`")
_LS_TIME_OPT = re.compile(r"[ \t]+--(?:full-time|time-style(?:=|[ \t]+)\S+|time=\S+)")


def _strip_fmt(fmt, directive):
    """A format string without its time directives and their labels/separators: '%n %s mtime %y' -> '%n %s';
    '%TY-%Tm-%Td %p\\n' -> '%p\\n'."""
    f = re.sub(_FMT_LABEL + r"(?:" + directive + r")", "\0", fmt)
    if "\0" not in f:
        return fmt
    for _ in range(8):
        g = re.sub(r"\0(?:[ \t]*[-:/T.+,][ \t]*|[ \t]+)\0", "\0", f)
        if g == f:
            break
        f = g
    f = re.sub(r"(?:[ \t]*[|,;=][ \t]*|\\+t|\t|[ \t]+)\0", "\0", f)
    f = re.sub(r"^\0(?:[ \t]*[|,;][ \t]*|\\+t|\t|[ \t]+)?", "", f)
    return f.replace("\0", "")


_PS_CMD_ESC = re.compile(r"(?:(?<=\\n)|(?<=\\t)|\b)ps\s+[^|;&\n\"\\]{0,200}")
_PS_GONE = re.compile(r"(?P<pre>(?:^|;|&&|\|\||\r?\n|\\+n|\bthen\b|\bdo\b|\\*\"|')\+*)(?P<sp>[ \t]*)\x01(?P<post>[ \t]*(?:;|&&|\|\|)[ \t]*)?")


def clock_read_commands(text):
    """stat/find/ps/ls asked for file or process times: the time directives, fields and options go (the rest of the
    listing stays); a $(stat ... %y ...) substitution is a clock read and becomes the removal token."""
    text = _STAT_SUBST.sub(REMOVED, text)
    text = _STAT_FMT.sub(lambda m: m.group(1) + m.group(2) + _strip_fmt(m.group(3), _STAT_TIME) + m.group(2), text)
    text = _FIND_FMT.sub(lambda m: m.group(1) + m.group(2) + _strip_fmt(m.group(3), _FIND_TIME) + m.group(2), text)
    text = _FIND_TIME_OPT.sub("", text)
    text = _LS_TIME_OPT.sub("", text)

    def ps(m):
        cmd = m.group(0)

        gone = []

        def fields(f):
            keep = [x for x in f.group(1).split(",") if x.rstrip("=").lower() not in _PS_TIME_FIELDS]
            if not keep:
                gone.append(1)
            return f.group(0)[:f.start(1) - f.start()] + ",".join(keep) if keep else ""
        new = _PS_FORMAT.sub(fields, cmd)
        return "\x01" if gone else new                 # `ps -p PID -o etime=`: nothing but a clock read
    text = _PS_CMD_ESC.sub(ps, text)
    if "\x01" in text:
        text = _sh_stmt_sub(_PS_GONE, text)
        text = text.replace("\x01", "")
    return text


_CLOCK_READ_CALL = re.compile(r"\bps\b|\bstat\b|-f?printf\b|\bls\b[^|;&\n]*[ \t]-\w*l|\bfind\b[^|;&\n]*-ls\b|\bdate\b|\bgit\s+log\b")


def removed_columns(body):
    """Output of a listing command (ps, stat, ls -l, find -printf) whose time column was removed: the removal token and
    its column separator go."""
    if REMOVED not in body and "|" not in body:
        return body
    body = re.sub(r"(?:[ \t]*\|[ \t]*|[ \t]+|\\+t|\t)" + _RM + r"(?=[ \t]|\\+t|\t|\\+[nr]|\r?\n|\\*\"|$)", "", body)
    body = re.sub(r"(?:(?<=^)|(?<=\n)|(?<=\\n)|(?<=\\\"))" + _RM + r"(?:[ \t]*\|[ \t]*|[ \t]+|\\+t|\t)", "", body)
    body = re.sub(r"(?<=\S)[ \t]+\|[ \t]*(?=\\+[nr]|\r?\n|\\*\"|$)", "", body)
    return body


def code_statements(text, counts_out=None, labels_out=None):
    names = _JS_CLOCK_VAR.findall(text)
    for p in _JS_CLOCK:
        text = p.sub("", text)
    if names:
        text = _clock_var_uses(text, names)
    text = sleep_loops(text, counts_out, labels_out)
    for p in _SLEEP_STMTS:
        text = p.sub("", text)
    text = _sh_stmt_sub(_SH_SLEEP, text)
    text = _SH_TIME_PREFIX.sub(lambda m: " " if m.start() and text[m.start() - 1] in "{(" else "", text)
    text = _SH_DATE_SUB.sub(REMOVED, text)
    text = _sh_stmt_sub(_SH_DATE, text)
    text = _SH_TIMEOUT.sub(lambda m: m.group("pre"), text)
    text = _PSUTIL_TIME_FIELD.sub("", text)
    text = _exit124(text)
    text = grep_alternatives(text)
    if _PROC_STAT_CALL.search(text):
        # awk over /proc/<pid>/stat printing the tick fields: `print $1, $14, $15` -> `print $1`
        text = re.sub(r"[ \t]*,[ \t]*(?:\\*\"[^\"\\]{0,30}\\*\"[ \t]*)?\$(?:1[4-7]|22)\b", "", text)
        text = _PROC_STAT_IDX.sub("", text)
    text = _imports(text)
    text = empty_items(text)
    text = clock_read_commands(text)
    return text


# --- code: time bookkeeping
BOOKKEEP = re.compile(
    r"(?i:[\w.-]*\b(?:started|finished|start|end|deadline)[-_](?:utc|epoch|time)\b[\w.-]*)"
    r"|(?i:\b\w*(?:elapsed|deadline|_cutoff|cutoff_|requested_(?:minutes?|seconds?|mins?|secs?|duration|work|task_period|"
    r"period|time|window|minimum|end)|full_requested|work[-_]?period|work[-_]?interval|work[-_]?window|audit_window|"
    r"[-_]timing|timing_|timing(?=\s*=|\[|\.get)|"
    r"observation_window|time_window|remaining_(?:seconds|minutes|time|secs|mins)|time_(?:left|remaining|used)|"
    r"timeUsed|started_?at|finished_?at|startedAt|finishedAt|(?:start|end|finish|finished|started|now|begin)_"
    r"(?:time|epoch|utc|ts|stamp)|_utc|minimum_(?:finish|end)|current_year|session_record|time_record|"
    r"(?:task|work|session|repro|run)_(?:start|end|begin|finish)(?![a-z])|time_?limit|time_?budget|planned_(?:stop|end|time))\w*)"
    r"|\bdatetime\.(?:now|utcnow|today|fromtimestamp|utcfromtimestamp)\s*\("
    r"|\bdatetime\(\s*" + _RECENT_YEAR +
    r"|\btime\.(?:time|monotonic|perf_counter|time_ns|monotonic_ns|strftime|localtime|gmtime|ctime|asctime)\s*\("
    r"|\bDate\.now\(\)|\bnew\s+Date\(|\bperformance\.now\(\)|\bSys\.time\(\)|\bproc\.time\(\)|\bSys\.Date\(\)"
    r"|\.isoformat\(|\bstrftime\(|\bstrptime\(|\bfromisoformat\(|\.total_seconds\(\)"
    r"|\bclock__\w+|\bcurr_time\b|\bcurrent_time\b|\bagenttime_clock\w*|\btimer\d*\b|\bTimeHistory\b"
    r"|\{[^{}\n]{1,80}\}\s?(?:s|ms|sec|secs|seconds|min|mins|minutes|h|hrs|hours)(?:/\w+)?\b"
    r"|\\*[\"'](?:Started|Finished|Start(?:ed)?\s+at|Finished\s+at|Start\s+time|End\s+time|Began|Completed\s+at|Elapsed)\s*:"
    r"|\[\s*\\*['\"]\w*(?:time|seconds|minutes|elapsed|duration|timeout|_utc|epoch|deadline)\w*\\*['\"]\s*\]\s*=(?!=)"
    r"|\b(?:task|work|session|repro|run|job|budget|timer|clock|review|period)(?:Start|End|Begin|Finish|Stop|Deadline)\w*"
    r"|\b[a-z]\w*(?:(?:Start|End|Begin|Stop|Now|Unix|Finish)Epoch|Deadline)(?:Ms|Sec|Secs|Seconds)?\b"
    r"|\b\w*utc_?now\w*|\bnow_(?:utc|iso|str|ts)\w*|\biso_?now\w*|\btimestamp_?now\w*"
    r"|%[YmdHMS][-:/ T]%[YmdHMSz]|\{\w+:%[A-Za-z]"
    r"|\bSys\.timezone\(\)|\btzcode\b|\bSys\.getenv\(\s*\\*[\"']TZ\b"
    r"|\bSC_CLK_TCK\b|\bCLK_TCK\b|\bst_[amc]time(?:_ns)?\b|\bst_birthtime\b|\bget[amc]time\s*\(|\bcpu_times\b|\bcreate_time\b"
    r"|\bwhich\(\s*\\*['\"]timeout|\bcommand\s+-v\s+timeout\b|\bwhich\s+timeout\b"
    r"|\$\(\(\s*\$?\{?(?:end|stop|finish|now|t1|after|done)\w*\}?\s*-\s*\$?\{?(?:start|begin|t0|before)\w*\}?\s*\)\)"
    r"|\b\w+_(?:seconds|secs|millis|minutes|mins)\b|\btimings?\b(?=\s*(?:=|\[|\.|\)|,|:))"
    # per-step duration lists, bash's $SECONDS timer, clock differences against a start mark
    r"|(?:\bself\.)?\b\w*durations\b|\$\{?SECONDS\}?|\bSECONDS=|\b(?:t1|t2|t_end|tend|toc|now)\s*-\s*(?:t0|tic|t_start|tstart)\b")
_WRAPPER = re.compile(r"tools\.\w+\(|\(\s*\{\s*\\*\"?(?:cmd|command|code|chars|input)\b|"
                      r"\\*\"\s*,\s*\\*\"?\w+\\*\"?\s*:\s*[\d\\\"]|\b(?:cmd|command|code)\s*:\s*\\*\"")
_SEG = re.compile(r"\r\n|\n|\r|\\+[nr]")


def _segments(text):
    """(start, end, sep_end) for each line; separators are real or escaped newlines / carriage returns."""
    pos = 0
    for m in _SEG.finditer(text):
        yield pos, m.start(), m.end()
        pos = m.end()
    yield pos, len(text), len(text)


def _balanced(s):
    return all(s.count(a) == s.count(b) for a, b in ("()", "[]", "{}"))


_TIMEISH_NAME = re.compile(r"(?i)^(?:t\d|t_?start|t_?end|tic|toc|start\w*|end_?\w*|begin\w*|began|stop_?\w*|finish\w*|"
                           r"stamp\w*|\w*_?(?:time|times|ts|epoch|utc|stamp|deadline|elapsed|clock|now|timing|timings)|"
                           r"deadline\w*|elapsed\w*|now\w*|timing\w*|interval|durations?)$")
_ASSIGN_NAME = re.compile(r"^\s*\+?\s*(?:(?:const|let|var|local|export)\s+)?([A-Za-z_]\w*)\s*(?:<-|:=|=(?!=))")


_GENERIC_CALLS = {"np", "numpy", "float", "int", "round", "max", "min", "len", "sum", "list", "math", "abs", "str", "self",
                  "timer", "time", "datetime", "sorted", "statistics", "mean", "median", "tuple", "dict", "json", "loads",
                  "dumps", "get", "append", "fsum", "e", "E"}


def _pure_time_rhs(rhs):
    """True if an assignment's right side is nothing but a clock/timing expression (`t.durations[1:]`), so the
    variable it names holds only time."""
    rest = BOOKKEEP.sub(" ", rhs)
    if rest == rhs:
        return False
    ids = set(re.findall(r"[A-Za-z_]\w*", re.sub(r"\\[ntr]|'[^']*'|\"[^\"]*\"", " ", rest))) - _GENERIC_CALLS
    return not ids


def bookkeeping_lines(text, names_out=None):
    """Drop lines (or ;-statements) of time bookkeeping code; fall back to replacing the identifier itself. A line cut
    in two by an escaped newline inside a string literal is joined with its (short) continuation first. Time-named
    variables assigned on dropped lines are reported in names_out, so lines that use them can go too."""
    if not BOOKKEEP.search(text):
        return text
    segs = list(_segments(text))
    out = []
    k = 0
    while k < len(segs):
        a, b, c = segs[k]
        seg = text[a:b]
        k += 1
        if not BOOKKEEP.search(seg):
            out.append(text[a:c])
            continue
        if len(seg) <= 800 and not _WRAPPER.search(seg) and not _balanced(seg):
            j, joined = k, seg
            while j < len(segs) and j - k < 3 and not _balanced(joined):
                nxt = text[segs[j][0]:segs[j][1]]
                if len(nxt) > 40 or _WRAPPER.search(nxt):
                    break
                joined += "\n" + nxt
                j += 1
            if _balanced(joined) and j > k:
                k = j
                continue
        parts = re.split(r"(?<=[.!?])[ \t]+(?=[A-Z\\])", seg)
        if len(parts) > 1 and len(_PROSE_WORD.findall(seg)) >= 4:
            keep = [x for x in parts if not BOOKKEEP.search(x)]
            if keep and len(keep) < len(parts):         # a prose line: only its sentence with the time value goes
                out.append(" ".join(keep) + text[b:c])
                continue
        if len(seg) <= 800 and not _WRAPPER.search(seg) and _balanced(seg):
            nm = _ASSIGN_NAME.match(seg)
            if names_out is not None and nm and (_TIMEISH_NAME.match(nm.group(1)) or _pure_time_rhs(seg[nm.end():])):
                names_out.add(nm.group(1))
            # a prose line (a wrapped sentence in a note or patch) continues on the next line: that clause goes too
            if (k < len(segs) and len(_PROSE_WORD.findall(seg)) >= 4 and not re.search(r"[.!?:;]\s*$", seg)):
                na, nb, nc = segs[k]
                nxt = text[na:nb]
                cm = re.match(r"(\s*\+?\s*)([a-z][^.;!?]*[.;!?])(?:\s+|$)", nxt)
                if cm:
                    rest = nxt[cm.end():]
                    if rest.strip():
                        out.append(cm.group(1) + rest[:1].upper() + rest[1:] + text[nb:nc])
                    k += 1
            continue                                   # drop line and its separator
        # statement level: split on '; ' outside obvious wrappers
        parts = re.split(r"(;[ \t]*)", seg)
        kept, changed = [], False
        for i in range(0, len(parts), 2):
            st = parts[i]
            sep = parts[i + 1] if i + 1 < len(parts) else ""
            if BOOKKEEP.search(st) and len(st) <= 400 and not _WRAPPER.search(st) and _balanced(st):
                changed = True
                nm = _ASSIGN_NAME.match(st)
                if names_out is not None and nm and _TIMEISH_NAME.match(nm.group(1)):
                    names_out.add(nm.group(1))
                continue
            kept.append(st + sep)
        seg2 = "".join(kept) if changed else seg
        seg2 = BOOKKEEP.sub(REMOVED, seg2)
        out.append(seg2 + text[b:c])
    return "".join(out)


# --- code left hollow by the scrub
_BLOCK_HEAD = re.compile(r"^(?P<pre>\+?)(?P<ind>[ \t]*)(?:async\s+)?(?P<kw>def|class)\s+(?P<name>\w+)\b[^\n]*:\s*$")
_PAREN_HEAD = re.compile(r"^(?P<pre>\+?)(?P<ind>[ \t]*)(?P<name>[A-Za-z_]\w*)\s*=\s*[(\[{]\s*$")
_TIMER_CLASS = re.compile(r"(?i)timer|timing|stopwatch|timehistory|clock")
_TIMER_DEF = re.compile(r"(?i)^(?:timestamp|utc_?now|now_(?:utc|iso|str)|iso_?now|timestamp_?now|elapsed\w*|\w+_elapsed|"
                        r"timer|stopwatch|\w*_timer|timed)$")


def _ind(line, pre_len):
    body = line[pre_len:]
    return len(body) - len(body.lstrip(" \t")), body.strip()


def _orig_block_empty(orig, header):
    """True if `header` (a def/class line) had an empty block in the text before scrubbing too (or is not found)."""
    lines = [orig[a:b] for a, b, _ in _segments(orig)]
    for i, line in enumerate(lines):
        if line == header:
            m = _BLOCK_HEAD.match(line)
            pl, ind = len(m.group("pre")), len(m.group("ind").expandtabs())
            for nxt in lines[i + 1:]:
                ji, js = _ind(nxt, pl if nxt[:pl] == m.group("pre") else 0)
                if js:
                    return ji <= ind or js in ("...", "…")
            return True
    return True


def hollow_code(text, names=None, orig=None):
    """Python definitions left with an empty body by the scrub (`def stamp():` whose only line was a clock read),
    timing classes and helpers (class StepTimer, def utc_now), and `x = (` ... `)` assignments whose content was all
    removed go whole; then the lines that use their names (and time variables dropped as bookkeeping) go too."""
    names = set(names or ())
    if not (names or re.search(r"\b(?:def|class)\s+\w+|\w\s*=\s*[(\[{][ \t]*(?:\r?\n|\\+n)", text)):
        return text
    for _ in range(4):
        segs = list(_segments(text))
        lines = [text[a:b] for a, b, _ in segs]
        drop = set()
        for i, line in enumerate(lines):
            m = _BLOCK_HEAD.match(line) or _PAREN_HEAD.match(line)
            if not m:
                continue
            pl = len(m.group("pre"))
            ind = len(m.group("ind").expandtabs())
            j, body = i + 1, []
            if "kw" in m.groupdict() and m.groupdict().get("kw"):
                while j < len(lines):
                    ji, js = _ind(lines[j], pl if lines[j][:pl] == m.group("pre") else 0)
                    if js and ji <= ind:
                        break
                    if js:
                        body.append(js)
                    j += 1
                timer = (m.group("kw") == "class" and _TIMER_CLASS.search(m.group("name"))) or \
                        (m.group("kw") == "def" and _TIMER_DEF.match(m.group("name")))
                if body and not timer:
                    continue
                if not body and not timer and (orig is None or _orig_block_empty(orig, line)):
                    continue                               # it was empty (or elided) before the scrub
            else:                                          # `x = (` ... `)`: gutted only if nothing but brackets/quotes
                while j < len(lines) and j - i < 12:
                    js = lines[j][pl:].strip() if lines[j][:pl] == m.group("pre") else lines[j].strip()
                    if re.match(r"[)\]}]", js):
                        break
                    body.append(js)
                    j += 1
                if j >= len(lines) or any(re.search(r"[A-Za-z0-9]", x.replace(REMOVED, "")) for x in body):
                    continue
                j += 1
            while j > i + 1 and not lines[j - 1].strip(" \t+"):
                j -= 1                                     # keep the blank lines after the block
            drop.update(range(i, j))
            names.add(m.group("name"))
        if names:
            pat = re.compile(r"(?<![\w.])(?:" + "|".join(re.escape(n) for n in sorted(names)) + r")\b")
            for i, line in enumerate(lines):
                if i in drop or not pat.search(line):
                    continue
                if len(line) <= 400 and not _WRAPPER.search(line) and _balanced(line):
                    drop.add(i)
        if not drop:
            return text
        text = "".join(text[a:c] for k, (a, b, c) in enumerate(segs) if k not in drop)
    return text


def _value_end(s, i):
    """End index of a code value starting at s[i] (quoted string, or text up to , } ) ] / newline at depth 0)."""
    m = re.match(r"(\\*)([\"'])", s[i:])
    if m:
        tok = m.group(0)
        close = re.compile(r"(?<!\\)" + re.escape(tok))
        k = close.search(s, i + len(tok))
        if not k:
            return None
        j = k.end()
        # allow method calls / concatenation after the string, e.g. '...'.format(x)
        return j
    depth, j = 0, i
    while j < len(s):
        ch = s[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif ch == "," and depth == 0:
            return j
        elif ch == "\n" or (ch == "\\" and s[j:j + 2] in ("\\n", "\\r")) and depth == 0:
            return j
        elif ch in "\"'":
            e = _value_end(s, j)
            if e is None:
                return None
            j = e
            continue
        j += 1
    return j


_PAIR = re.compile(r"(?P<lead>[{,(][ \t]*(?:(?:\r?\n|\\+n)\+?[ \t]*)*)(?P<q>\\*[\"']?)(?P<key>[A-Za-z_][\w\-]{1,60})(?P=q)"
                   r"[ \t]*(?P<sep>:|=(?!=))[ \t]*")


# a value computed from a clock or a file/process time: st_mtime_ns, p.info['cpu_times'], time.time() - t0
_TIME_EXPR = re.compile(r"\bst_[amc]time|\bget[amc]time|\bcpu_times\b|\bcreate_time\b|\btime\.(?:time|monotonic|perf_counter)"
                        r"|\bdatetime\.|\bDate\.now|\bSys\.time|\belapsed\b|\butc_?now|\.isoformat\(|\bstrftime\(")
_PSUTIL_TIME_FIELD = re.compile(r"\\*['\"](?:cpu_times|create_time)\\*['\"]\s*,\s*|,\s*\\*['\"](?:cpu_times|create_time)\\*['\"]")


def pair_removal(text):
    """Delete `key: value` / `key=value` pairs with time or budget keys inside {...} / (...) in code text."""
    for _ in range(8):                                  # adjacent pairs share a comma: repeat until stable
        new = _pair_pass(text)
        if new == text:
            return new
        text = new
    return text


def _pair_pass(text):
    if not re.search(r"(?i)time|elapsed|duration|deadline|cutoff|utc|epoch|started|finished|requested|sleep|"
                     r"timeout|seconds|minutes|_ms\b|created|updated|date", text):
        return text
    out, pos = [], 0
    for m in _PAIR.finditer(text):
        if m.start() < pos:
            continue
        key = m.group("key")
        if not _pair_key(key):
            continue
        if m.group("sep") == "=" and m.group("lead")[0] == "{" and not m.group("q"):
            continue                                    # "{x=1" is not a pair context we know
        vs = m.end()
        ve = _value_end(text, vs)
        if ve is None or ve == vs:
            continue
        val = text[vs:ve].strip()
        if (not is_budget_key(key) and not _LITERAL.fullmatch(val) and not _TIME_CONTAINER.match(key)
                and not _TIME_EXPR.search(val) and not is_strong_time_key(key)):
            continue                                    # "{'time': row.t}" is task data, not a time value
        if (m.group("lead")[0] == "{" and not m.group("q")
                and re.fullmatch(r"[<>^=+\-#.,_][<>^=+\-#0-9.,_]*[bcdeEfFgGnosxX%]?|[0-9.,_]*[bcdeEfFgGnosxX%]",
                                 text[vs:ve])):
            continue                                    # f-string format spec, "{time:.17g}"
        ks = m.start() + len(m.group("lead"))
        after = re.match(r"[ \t]*,[ \t]*(?:(?:\r?\n|\\+n)\+?[ \t]*)?", text[ve:])
        if after:                                       # "{a:1, key:v, b:2}" -> "{a:1, b:2}"
            out.append(text[pos:ks])
            pos = ve + after.end()
        elif m.group("lead")[0] == ",":                 # "{a:1, key:v}" -> "{a:1}"
            out.append(text[pos:m.start()])
            pos = ve
        else:                                           # "{key:v}" -> "{}"
            out.append(text[pos:ks])
            pos = ve
    out.append(text[pos:])
    return "".join(out)


# --- after token replacement: shells and residues
_LABELS = (r"(?:(?:run\s+|work\s+|task\s+|session\s+)?(?:started|finished|ended|began|begun|completed|done|stopped)"
           r"(?:\s+at|\s+on)?|start(?:ed)?(?:\s+time)?|end(?:ed)?(?:\s+time)?|finish(?:ed)?(?:\s+time)?|as\s+of|at|since|"
           r"until|by|on|mtime|ctime|atime|(?:last\s+)?modified|created|updated|timestamp|time|date|now|utc|clock|"
           r"elapsed|duration|took|waited|wall(?:\s+time)?|(?:total\s+)?(?:wall\s+)?time|up\s+to|in|after|for|every|"
           r"within|over|it\s+is|current\s+time|deadline|eta|runtime|(?:at\s+)?about|around|approx\.?|"
           r"(?:a|the)\s+full|full|remaining|left)")
_LABEL_SHELL = re.compile(r"(?i)[ \t]*[,;]?[ \t]*\b" + _LABELS + r"[ \t]*[:=]?[ \t]*[~≈]?[ \t]*\(?" + _RM + r"\)?"
                          r"(?:[ \t]+(?:effort|period|window|session|interval|seconds?|minutes?|s)\b)?")
_LABEL_WORDS = re.compile(r"(?i)\b(?:started|start|finished|finish|ended|end|began|completed|done|time|date|now|utc|"
                          r"elapsed|took|wall|total|run|it|is|at|as|of|current|mtime|ctime|modified|created|updated|"
                          r"timestamp|deadline|eta|in|after|for|waited|since|until|up|to|duration|runtime|clock|"
                          r"seconds?|minutes?|s|ms|last|work|task|session|the|a|full|stamp|about|approx)\b")
_CURL_LINE = re.compile(r"^\s*(?:%\s+Total\s+%\s+Received|Dload\s+Upload\s+Total\s+Spent|"
                        r"\d{1,3}\s+[\d.]+[kMGT]?\s+\d{1,3}\s+[\d.]+[kMGT]?\s+\d{1,3}\s+[\d.]+[kMGT]?\s+[\d.]+[kMGT]?\s+"
                        r"[\d.]+[kMGT]?\s+)")
_RESIDUE = [
    (re.compile(r"(?<![\d:])(?:\d{1,2}:)+" + _RM), REMOVED),                    # "17:[removed]", "00:00:[removed]"
    (re.compile(_RM + r"[ \t,]+(?:19|20)\d{2}(?![\w.])"), REMOVED),             # "[removed] 2026"
    (re.compile(_RM + r"[ \t]*[-–—/][ \t]*(?:19|20)\d{2}(?!\d)"), REMOVED),       # "[removed]–2027", "[removed]/2027"
    # a date stamp in a name or slug goes with its joiner: "rename-[removed]" -> "rename", "df_[removed].csv" -> "df.csv"
    (re.compile(r"(?<=[A-Za-z0-9])[-_.]" + _RM + r"(?=[\]\s.\-_/)\"'|,;:`]|\\|$)"), ""),
    (re.compile(r"(?<=/)" + _RM + r"[-_](?=[A-Za-z])"), ""),                          # "/[removed]-notes.md" -> "/notes.md"

    (re.compile(r"(?<![\w.])(?:19|20)\d{2}[ \t,]+" + _RM), REMOVED),
    (re.compile(r"\[" + _RM + r"\]"), REMOVED),
    # a removed per-step time between " - " fields ("20/20 - [removed] - loss: ...") or after a size ("2.1 MB [removed]")
    (re.compile(r"[ \t]+-[ \t]+" + _RM + r"(?=[ \t]+-[ \t]|[ \t]*(?:$|\r?\n|\\+[nr]|\\*\"))"), ""),
    (re.compile(r"(?<=[kKMGT]B)[ \t]+" + _RM + r"(?=[ \t]*(?:$|\r?\n|\\+[nr]|\\*\"))"), ""),
    (re.compile(r"`" + _RM + r"`"), REMOVED),
    (re.compile(r"[ \t]*\((?:as\s+of\s+|on\s+|at\s+)?" + _RM + r"\)"), ""),                # "Environment ([removed]):"
    # a short prose aside holding a removed value: "is fine (checked [removed] ago)." -> "is fine."
    (re.compile(r"(?<=[A-Za-z.,])[ \t]*\((?=[^()\n]{0,60}\))(?:[A-Za-z,;\-]+[ \t]+){0,3}" + _RM +
                r"(?:[ \t]+[A-Za-z,;\-]+){0,3}\)"), ""),
    (re.compile(r"(?<=[^\n])(?<!\\n)[+\-]" + _RM), REMOVED),         # "+[removed]" (a sign), not a patch line's "+"
    (re.compile(r"(?<=[\"'])" + _RM + r"[ \t]+(?=[A-Z])"), ""),              # echo "[removed] START ..." prefix
    (re.compile(r"(?<![\w.\-/])\d{1,2}[-/.]\d{1,2}[-/.]?" + _RM), REMOVED),        # "01-08[removed]"
    (re.compile(r"\[\]\([^()\s]*\)"), ""),                                       # markdown link emptied: [](x.md)
    # a removed date directory in a path goes with its slash: sessions/[removed]/rollout.jsonl
    (re.compile(r"(?<=/)" + _RM + r"/"), ""),
    # Keras progress bars: " - ETA" left without its value, "] - - loss" where "ETA: 0s" was
    (re.compile(r"[ \t]*-[ \t]*ETA:?[ \t]*(?:" + _RM + r")?(?=[ \t]*(?:$|\r?\n|\\+[nr]|\\*\"|[ \t]+\d))"), ""),
    (re.compile(r"(?<=[-*+] )\[\](?!\()"), ""),
]
_EMPTY_HEAD = re.compile(r"(?im)^[ \t]*\+?[ \t]*#{1,6}(?:[ \t]*|[ \t]+(?:Timing|Timings|Runtime|Run ?time|Durations?|Elapsed(?: time)?|"
                         r"Schedule|Time(?: budget| log| record| spent| used)?|Work period|Timeline|Session time)\b[^\n\\]{0,30})"
                         r"[ \t]*:?[ \t]*$")
_LOG_PREFIX = re.compile(r"^(\s*[+>]*\s*)" + _RM + r"(?:[ \t]*[|:,\]/][ \t]*|[ \t]+-[ \t]+|[ \t]+)(?=\S)(?!" + _RM + r")")
_LABEL_ONLY = re.compile(r"^[\s+>$#*\-]*[\w .\-\"'/()]{1,40}?\s*[:=]\s*[~≈\"'`(]*" + _RM + r"[)\"'`]*[\s,;.]*$")
_ESTIMATE = re.compile(r"(?i)(?<![\w.])[~≈][ \t]*" + _RM + r"|\b(?:projected|estimated?|steady-state|expect(?:ed)?|takes?|took|needs?|"
                       r"roughly|about|approx(?:imately|\.)?)[ \t]+[~≈]?[ \t]*" + _RM + r"|=>[ \t]*[~≈]?[ \t]*" + _RM)
_BULLET_ONLY = re.compile(r"^[ \t]*\+?[ \t]*[-*+][ \t]*[.,;:]?[ \t]*$")
_ESTIMATE_LINE = re.compile(r"^[\s\-*+.,;:>]*" + _RM + r"[\s.,;:]*\(")      # "- [removed] (20 steps)"


def _stat_siblings(text):
    """Inside (...) that holds a removed value, min/max/mean stats are the same timing numbers."""
    def f(m):
        inner = m.group(1)
        if REMOVED not in inner:
            return m.group(0)
        new = re.sub(r"(?i)\b((?:min|max|mean|avg|median|std|p50|p90|p95|p99)\s*[=:]?\s*)(\d+(?:\.\d+)?)(?![\w.])",
                     lambda x: x.group(1) + REMOVED, inner)
        return "(" + new + ")"
    return re.sub(r"\(([^()\n]{0,200})\)", f, text)


_BARE_KEY = re.compile(r"[ \t]*\+?[ \t]*(?:[-*+][ \t]+)?[A-Za-z][\w-]{0,30}:[ \t]*")


def _estimate_residue(seg, max_line):
    """A line holding a removed runtime estimate ("~[removed]", "projected [removed]", "- [removed] (20 steps)"):
    drop the sentences that hold it; drop the whole line only if nothing else is left or it is short.
    Returns the new line, or None to drop it."""
    if REMOVED not in seg or not (_ESTIMATE.search(seg) or _ESTIMATE_LINE.match(seg)):
        return seg
    parts = re.split(r"(?<=[.!?;])[ \t]+", seg)
    if len(parts) > 1:
        kept = [x for x in parts if not (REMOVED in x and (_ESTIMATE.search(x) or _ESTIMATE_LINE.match(x)))]
        if kept and re.search(r"[A-Za-z]{3}", " ".join(kept).replace(REMOVED, "")):
            return " ".join(kept)
    return None if len(seg) <= max_line else seg


_VERB_AT = re.compile(r"(?i)\b((?:finished|started|completed|ended|began|stopped|launched|exited|ran|created|modified|"
                      r"updated|written|saved|done)(?:\s+successfully)?)[ \t]+(?:at|on)[ \t]+`?" + _RM + r"`?")
_OPEN_SHELL = re.compile(r"(?:(?<=^)|(?<=\n)|(?<=\\n)|(?<=[.!?:][ \t])|(?<=\*\*[ \t])|(?<=\\\")|(?<=\"))([ \t]*)(?:As\s+of|"
                         r"Since|On|At|By|Until|Before|After|From)[ \t]+`?" + _RM + r"`?,?[ \t]+([a-z])")
_MID_SHELL = re.compile(r"(?:,[ \t]*|[ \t]+)(?:on|as\s+of|since)[ \t]+`?" + _RM + r"`?(?=[ \t,.;:)]|$)")


def shell_trim(text, mid=True):
    """Removed dates cut out cleanly where grammar allows: "finished at [removed] with exit code 0" -> "finished with
    exit code 0"; "As of [removed] the container has" -> "The container has"."""
    for p, r in _RESIDUE:
        text = p.sub(r, text)
    text = _COLLAPSE.sub(REMOVED, text)
    text = _VERB_AT.sub(r"\1", text)
    text = _OPEN_SHELL.sub(lambda m: m.group(1) + m.group(2).upper(), text)
    if mid:
        text = _MID_SHELL.sub("", text)
    return text


_WRAPPED_VALUE = re.compile(r"(?i)\b(took|takes|lasted|needed|needs|required|requires|spent|in|after|for|about|roughly|approximately)"
                            r"[ \t]*(?:\r?\n|\\+n)[ \t]*\+?[ \t]*(?=" + _RM + r")")
_RUSAGE_LINE = re.compile(r"[ \t]*\+?[ \t]*(?:real|user|sys)(?:[ \t]|\\+t)+(?:" + _RM + r"|[\dms.:]+)[ \t]*")
_EMPTY_LEFT = re.compile(r"[ \t]*\+?[ \t]*(?:[rbuf]?(?:\\*\"){6}|[rbuf]?'{6}"
                         r"|(?:print|echo|text|console\.log|cat)\(?[ \t]*(?:f?\\*[\"'](?:\\+n)?\\*[\"'])?[ \t]*\)?[ \t]*;?"
                         r"|[\w.]+\.write\([ \t]*f?\\*[\"'](?:\\+n)?\\*[\"'][ \t]*\)[ \t]*;?"
                         r"|(?:[-*][ \t]+)?\*\*[^*\n]{1,60}\*\*:?)[ \t]*")


# a write/print of nothing but a newline, left when its text went: f.write(f'\n') (its "\n" would split a line view)
_HOLLOW_WRITE = re.compile(r"(?P<br>\r?\n|\\+n)(?P<ind>[ \t]*)(?:[\w.]+\.write|print)\([ \t]*f?(?P<q>\\*[\"'])(?:\\+n)+(?P=q)[ \t]*\)"
                           r"[ \t]*;?(?=\r?\n|\\+n|$)")
_HDR_BEFORE = re.compile(r"(?:^|\r?\n|\\+n)(?P<ind>[ \t]*)(?:with|for|while)\b[^\n\\]*:[ \t]*$")


def hollow_writes(text, orig):
    """Drop f.write(f'\n') / print('\n') statements the scrub emptied, and a `with ...:` header left with no body."""
    if not _HOLLOW_WRITE.search(text):
        return text
    known = {m.group(0).strip() for m in _HOLLOW_WRITE.finditer(orig)}
    out, pos = [], 0
    for m in _HOLLOW_WRITE.finditer(text):
        if m.group(0).strip() in known:
            continue
        head = text[pos:m.start()]
        h = _HDR_BEFORE.search(head)
        nxt = re.match(r"(?:\r?\n|\\+n)([ \t]*)\S", text[m.end():])
        if h and len(m.group("ind")) > len(h.group("ind")) and (not nxt or len(nxt.group(1)) <= len(h.group("ind"))):
            head = head[:h.start()] + (h.group(0)[:len(h.group(0)) - len(h.group(0).lstrip("\r\n\\n"))]
                                       if h.start() == 0 else "")
        out.append(head)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


_BLANK_PATCH_RUN = re.compile(r"(?:(?<=^)|(?<=\n)|(?<=\\n))\+[ \t]*(?:(?:\r?\n|\\+n)\+[ \t]*)+(?=\r?\n|\\+n|$)")
_BLANK_RUN = re.compile(r"(?:(?:\\+n)[ \t]*){3,}|(?:\r?\n[ \t]*){3,}")


_CONTAINER_KEY = re.compile(r"(?P<q>\\*[\"'])(?P<key>[A-Za-z_][\w-]{0,60})(?P=q)[ \t]*:[ \t]*(?P<open>[{\[])")


def container_blocks(text):
    """A pretty-printed JSON block under a timing key inside plain text ("timing": {"started_utc": ..., ...},) goes
    whole, with its comma, so no emptied braces are left."""
    if not re.search(r"(?i)timing|timestamps|durations|elapsed|clock|deadline|schedule|work_period", text):
        return text
    out, pos = [], 0
    for m in _CONTAINER_KEY.finditer(text):
        if m.start() < pos or not _TIME_CONTAINER.match(m.group("key")):
            continue
        o = m.group("open")
        c = "}" if o == "{" else "]"
        depth, j = 0, m.end() - 1
        while j < len(text):
            if text[j] == o:
                depth += 1
            elif text[j] == c:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(text):
            continue
        end = j + 1
        s0 = m.start()
        after = re.match(r"[ \t]*,[ \t]*(?:(?:\r?\n|\\+n)\+?[ \t]*)?", text[end:])
        if after:
            end += after.end()
        else:
            before = re.search(r",[ \t]*(?:(?:\r?\n|\\+n)\+?[ \t]*)?$", text[pos:s0])
            if before:
                s0 = pos + before.start()
        out.append(text[pos:s0])
        pos = end
    out.append(text[pos:])
    return "".join(out)


_EMPTIED_PAIR = re.compile(r"(?P<lead>[{,][ \t]*(?:(?:\r?\n|\\+n)\+?[ \t]*)*)(?P<q>\\*[\"'])(?P<key>[\w .-]{1,60}?)(?P=q)[ \t]*:[ \t]*"
                           r"(?P<q2>\\*[\"'])(?P=q2)")


def emptied_pairs(text, orig):
    """`"note": ""` where the scrub emptied the value: the pair goes (a blank value marks a removal)."""
    if not _EMPTIED_PAIR.search(text):
        return text
    known = {m.group("key") for m in _EMPTIED_PAIR.finditer(orig)}
    out, pos = [], 0
    for m in _EMPTIED_PAIR.finditer(text):
        if m.start() < pos or m.group("key") in known:
            continue
        ks = m.start() + len(m.group("lead"))
        after = re.match(r"[ \t]*,[ \t]*(?:(?:\r?\n|\\+n)\+?[ \t]*)?", text[m.end():])
        if after:
            out.append(text[pos:ks])
            pos = m.end() + after.end()
        else:                                          # the last pair: the comma before it goes
            out.append(text[pos:m.start()])
            pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def blank_runs(text, orig):
    """Blank lines piled up where scrubbed lines were (in a patch: "+\n+\n+\n+") shrink to one blank line, unless
    the original text already had such runs."""
    if not _BLANK_PATCH_RUN.search(orig):
        text = _BLANK_PATCH_RUN.sub("+", text)
    if not _BLANK_RUN.search(orig):
        text = _BLANK_RUN.sub(lambda m: ("\\n\\n" if "\\" in m.group(0) else "\n\n"), text)
    return text


def post_clean(text, before, max_line=400):
    """Remove label shells around removed values and lines left with nothing but removed values or labels.
    `before` is the set of lines of the text before scrubbing (lines that were already there are not "left over")."""
    if (all(text[a:b] in before for a, b, _ in _segments(text))
            and REMOVED not in text and not _CURL_LINE.search(text) and "[]" not in text
            and not _EMPTY_HEAD.search(_NLV.sub("\n", text))
            and not any(_BARE_KEY.fullmatch(text[a:b]) for a, b, _ in _segments(text))):
        return text
    # "took\n[removed]": a removed duration wrapped onto the next line belongs to the line that announces it
    text = _WRAPPED_VALUE.sub(r"\1 ", text)
    for p, r in _RESIDUE:
        text = p.sub(r, text)
    text = _COLLAPSE.sub(REMOVED, text)
    text = _stat_siblings(text)
    out = []
    for a, b, c in _segments(text):
        seg = text[a:b]
        if _RUSAGE_LINE.fullmatch(seg):
            continue
        if seg not in before and seg.strip() and (_EMPTY_LEFT.fullmatch(seg) or not seg.strip(" \t+")):
            continue        # a line the scrub left hollow: an empty docstring, print('') / f.write(f'\n'), a bold label
        if _CURL_LINE.match(seg):
            continue
        if _EMPTY_HEAD.fullmatch(seg) and (seg not in before or re.search(r"(?i)[a-z]", seg)):
            continue
        if (_BULLET_ONLY.fullmatch(seg) or _BARE_KEY.fullmatch(seg)) and seg not in before:
            continue
        if REMOVED in seg:
            seg = _LOG_PREFIX.sub(r"\1", seg)
            if _LABEL_ONLY.fullmatch(seg):
                continue
            rest = _LABEL_WORDS.sub(" ", seg.replace(REMOVED, " "))
            words = re.findall(r"[A-Za-z0-9]+", rest)
            if not words or (len(words) <= 3 and any(re.search(r"(?i)date|time|modif|creat|access|change|birth|stamp|utc|^tz|zone",
                                                               w) for w in words)):
                continue
            if len(re.split(r"(?<=[.!?;])[ \t]+", seg)) > 1 and _ESTIMATE.search(seg):
                new = _estimate_residue(seg, 0)             # prose: "The full run took [removed]." goes as a sentence
                if new is not None:
                    seg = new
            new = _LABEL_SHELL.sub("", seg)
            if new != seg:
                new = re.sub(r"\(\s*,\s*", "(", new)
                new = re.sub(r"\s+([,.;:])(?=\s|$)", r"\1", new)
                if not re.search(r"[A-Za-z0-9]", _LABEL_WORDS.sub(" ", new)) and re.search(r"[A-Za-z0-9]", seg.replace(REMOVED, "")):
                    continue
                seg = new
            seg = _estimate_residue(seg, max_line)
            if seg is None:
                continue
        out.append(seg + text[b:c])
    return "".join(out)


# --- JSON bodies
class Ctx:
    def __init__(self, req_min, req_rules):
        self.req_min, self.req_rules = req_min, req_rules
        self.loop_counts = set()                       # sizes of waiting loops seen so far in the session
        self.loop_labels = set()                       # labels waiting loops print before their counter


_CLOCK_KEYS = {"current_time", "utc", "now", "time", "current_utc", "timestamp"}


def _has_clock_key(obj):
    if isinstance(obj, dict):
        return any(k in _CLOCK_KEYS for k in obj) or any(_has_clock_key(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_clock_key(v) for v in obj)
    return False


def _is_goal_readout(obj):
    """Codex goal tool output ({"goal": {..., "timeUsedSeconds", "tokensUsed"}}): a clock read; also as one settled
    promise of Promise.allSettled ({"index": 1, "status": "fulfilled", "value": {"goal": ...}})."""
    if isinstance(obj, dict) and isinstance(obj.get("result"), dict) and ("index" in obj or "i" in obj):
        obj = obj["result"]                             # {"index": 2, "result": {"status": ..., "value": {"goal": ...}}}
    if isinstance(obj, dict) and isinstance(obj.get("value"), dict) and "status" in obj:
        obj = obj["value"]
    return (isinstance(obj, dict) and isinstance(obj.get("goal"), dict)
            and bool(set(obj["goal"]) & {"timeUsedSeconds", "tokensUsed", "createdAt", "updatedAt", "status"}))


def _loads(s):
    t = s.strip()
    if len(t) < 2 or t[0] not in "{[" or t[-1] not in "}]":
        return None
    try:
        return json.loads(t)
    except ValueError:
        return None


def _dumps(obj, like):
    ind = None
    m = re.search(r"\n( +)\S", like)
    if m and like.lstrip()[:1] in "{[" and "\n" in like.strip():
        ind = len(m.group(1))
    sep = (",", ": ") if ind else ((", ", ": ") if re.search(r"\"\s*:\s", like[:500]) else (",", ":"))
    return json.dumps(obj, ensure_ascii="\\u" in like, indent=ind, separators=sep)


def _trivial_text(s):
    s = re.sub(r"\\+[nrt]", " ", s)
    s = re.sub(r"(?i)script (?:completed|running with cell id \d+)|output:|\(no (?:content|output)\)|sleep completed\.?|"
               r"text|input_text|type", " ", s)
    return not re.search(r"[A-Za-z0-9]", s.replace(REMOVED, ""))


_SETTLED = re.compile(r"Promise\.all(?:Settled)?\(\s*\[")
_EMPTY_TOOL_ITEM = re.compile(r"^\s*(?:await\s+)?tools\.\w+\(\s*\{\s*\\*\"?(?:cmd|command)\\*\"?\s*:\s*\\*\"\\*\"[^{}]*\}\s*\)\s*$")


def _top_level_items(s, start):
    """(item spans, end index) of the array literal whose "[" is at s[start]."""
    depth, i, items, cur = 0, start, [], start + 1
    quote = None
    while i < len(s):
        ch = s[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                items.append((cur, i))
                return items, i
        elif ch == "," and depth == 1:
            items.append((cur, i))
            cur = i + 1
        i += 1
    return None, None


def prune_settled_array(body):
    """In an allSettled array of tool calls, an exec item whose command the scrub emptied goes; returns the new body
    and the positions removed (their results go too)."""
    gone = []
    for m in list(_SETTLED.finditer(body))[::-1]:
        items, end = _top_level_items(body, m.end() - 1)
        if not items:
            continue
        drop = [k for k, (a, b) in enumerate(items) if _EMPTY_TOOL_ITEM.match(body[a:b])]
        if not drop or len(drop) == len([1 for a, b in items if body[a:b].strip()]):
            continue
        keep = [body[a:b].strip() for k, (a, b) in enumerate(items) if k not in drop and body[a:b].strip()]
        nl = "\n" if "\n" in body[m.end():end] else ""
        body = body[:m.end()] + nl + ("," + nl).join(keep) + nl + body[end:]
        gone.extend(drop)
    return body, sorted(set(gone))


def prune_settled_result(body, gone):
    """Drop the settled results at the removed positions ({"index": k, ...} printed one per text item), renumber."""
    obj = _loads(body)
    if not isinstance(obj, list):
        return body
    new = []
    for it in obj:
        inner = _loads(it["text"]) if isinstance(it, dict) and isinstance(it.get("text"), str) else None
        k = inner.get("index", inner.get("i")) if isinstance(inner, dict) else None
        if isinstance(k, int) and k in gone:
            continue
        new.append(it)
    if len(new) == len(obj):
        return body
    return _dumps(_renumber_settled(new), body)


def _renumber_settled(items):
    """Promise.allSettled result items ({"index": i, "status": ...} printed one per text item) are renumbered 0..n-1
    once the settled clock/sleep/goal items are gone, so no gap shows where they were."""
    slots = []
    for k, it in enumerate(items):
        inner = _loads(it["text"]) if isinstance(it, dict) and isinstance(it.get("text"), str) else None
        if isinstance(inner, dict) and ("status" in inner or "result" in inner):
            key = "index" if isinstance(inner.get("index"), int) else "i" if isinstance(inner.get("i"), int) else None
            if key:
                slots.append((k, key, inner))
    if not slots or [inner[key] for _, key, inner in slots] == list(range(len(slots))):
        return items
    items = list(items)
    for n, (k, key, inner) in enumerate(slots):
        inner = {**inner, key: n}
        items[k] = {**items[k], "text": _dumps(inner, items[k]["text"])}
    return items


def _wait_description(d):
    """A tool call's one-line description that announces a wait: "Wait and poll X" -> "Poll X", "Wait for X, ..." ->
    "Check X, ...", "Stop run, wait, check Y" -> "Stop run, check Y"."""
    new = re.sub(r"^Wait(?:\s+briefly|\s+a\s+bit)?\s+(?:and|then)\s+(\w)", lambda m: m.group(1).upper(), d)
    new = re.sub(r"^Wait(?:\s+briefly|\s+a\s+bit)?\s+for\s+", "Check ", new)
    new = re.sub(r"(?i),?\s+(?:then\s+)?wait(?:\s+for\s+[^,]+?)?(?=,|\s+and\b|$)", "", new)
    # "Poll the log" / "Final poll of X" -> "Check ..." (a poll is a wait)
    new = re.sub(r"^(?:(?:Final|Last|Another|Re-?)\s*)?[Pp]oll(?:ing)?(?:\s+(?:of|for|on))?\s+(\w)", lambda m: "Check " + m.group(1), new)
    new = re.sub(r"(?i)\s+(?:and|then|,)\s+(?:re-?)?poll\b(?:\s+(?:the|for))?", " and check", new)
    return new


def _walk(obj, kind, ctx):
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            if v == 124 and not isinstance(v, bool) and re.search(r"(?i)exit|returncode|^rc$|status", k):
                continue                                # the key goes: a missing exit code reads as running
            if _pair_key(k) and not isinstance(v, (dict, list)) and _pair_value_ok(k, v):
                continue
            nv = _walk(v, kind, ctx)
            if k == "description" and isinstance(nv, str) and re.search(r"(?i)\bwait|\bpoll", nv):
                nv = _wait_description(nv)
            if isinstance(v, str) and v.strip() and not nv.strip():
                continue                                # value was time content only
            new[k] = nv
        return new
    if isinstance(obj, list):
        new = []
        for item in obj:
            if isinstance(item, dict) and any(isinstance(v, str) and len(v) < 60 and (CLOCK_TOOL.match(v) or "clock__" in v)
                                              for v in item.values()):
                continue
            if (isinstance(item, dict) and isinstance(item.get("text"), str) and len(item["text"]) < 600
                    and _has_clock_key(_loads(item["text"]))):
                continue
            if isinstance(item, dict) and isinstance(item.get("text"), str) and _is_goal_readout(_loads(item["text"])):
                continue
            ni = _walk(item, kind, ctx)
            if (isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip()
                    and _loads(item["text"]) is not None and isinstance(ni, dict)
                    and _trivial_text(ni.get("text", "")) and not _trivial_text(item["text"])):
                continue
            new.append(ni)
        return _renumber_settled(new)
    if isinstance(obj, str):
        inner = _loads(obj)
        if isinstance(inner, (dict, list)):
            return _dumps(_walk(inner, kind, ctx), obj)
        return scrub_text(obj, kind, ctx)
    return obj


def scrub_struct(body, kind, ctx):
    obj = _loads(body)
    if isinstance(obj, (dict, list)):
        lead = body[:len(body) - len(body.lstrip())]
        return lead + _dumps(_walk(obj, kind, ctx), body.strip())
    return scrub_text(body, kind, ctx)


# "=== run 2: timing ===" (an echo banner naming a time readout)
_BANNER_WORD = re.compile(r"([=-]{3}[^=\n\"\\]{0,60}?)[ \t]*[:,-]?[ \t]*\b(?:timestamps?|pacing|timing|elapsed|durations?|clock|runtime)\b"
                          r"[ \t]*(?=[^=\n\"\\]{0,40}[=-]{3})")


# echo/printf literals holding a removed clock value, and simple commands whose argument was a removed name
_ECHO_LIT = re.compile(r"(?P<pre>(?:^|;|&&|\|\||\r?\n|\\+n|\bthen\b|\bdo\b|\belse\b)\+*)(?P<sp>[ \t]*)(?:echo|printf)(?:[ \t]+-[neE]+)*"
                       r"[ \t]+(?P<q>\\*[\"'])(?P<lit>(?:(?!(?P=q)|\r?\n).){0,300}?)(?P=q)(?P<post>[ \t]*(?:;|&&|\|\|)[ \t]*)?")
_ECHO_TIMEWORD = re.compile(r"\b(?:started|start|finished|finish|ended|end|began|time|date|now|elapsed|took|waited|since|until|"
                            r"clock|at|on|as\s+of)\b|\bUTC\b|\b(?:Started|Finished|Time|Date|Now|Elapsed)\b")
_REMOVED_ARG = re.compile(r"(?P<pre>(?:^|;|&&|\|\||\r?\n|\\+n|\bthen\b|\bdo\b)\+*)(?P<sp>[ \t]*)(?:cat|head|tail|less|more|wc|stat|"
                          r"touch|rm|ls|du|file)\b[^;&|\n\\\"]*?" + _RM + r"[^;&|\n\\\"]*(?P<post>[ \t]*(?:;|&&|\|\|)[ \t]*)?")


def echo_literals(text):
    """`echo "=== START RUN [removed] ==="` -> `echo "=== START RUN ==="`; an echo left with no words, or only
    a time label ("run started: [removed]", "--- [removed] ---"), goes; `cat /results/[removed]` goes."""

    def f(m):
        lit = m.group("lit")
        if REMOVED not in lit:
            return m.group(0)
        left = re.sub(r"(?i)[ \t]*[,;]?[ \t]*(?:\b(?:up\s+to|about|after|for|in|at|on|since|until|now|elapsed|took|waited|"
                      r"started|finished|ended|began)\b[ \t]*[:=]?[ \t]*)?[:@=]?[ \t]*[~\u2248]?[ \t]*" + _RM +
                      r"(?:[ \t]*(?:s|sec|secs|seconds|min|mins|minutes)\b)?[ \t]*", " ", lit)
        left = re.sub(r"^[ \t;,:]+|[ \t;,:]+$", "", re.sub(r"  +", " ", left))
        words = re.findall(r"[A-Za-z0-9]+", left.replace("$", " "))
        if not words or (len(words) <= 5 and _ECHO_TIMEWORD.search(left)):
            if m.group("post"):
                return m.group("pre") + m.group("sp")
            return "" if m.group("pre") in (";", "&&", "||") else m.group("pre")
        q = m.group("q")
        return m.group(0)[:m.start("lit") - m.start()] + left + q + (m.group("post") or "")
    text = _ECHO_LIT.sub(f, text)
    if REMOVED in text:
        text = _sh_stmt_sub(_REMOVED_ARG, text)
    return text


# "sh: 1: time: not found" and the like: the agent tried to time a command
_TIME_TOOL_ERR = re.compile(r"(?:(?<=^)|(?<=\n)|(?<=\\n)|(?<=\\\"))[^\n\\\"]{0,60}?(?:/usr/bin/time|\btimeout|\btime)(?::|[ \t]+command)"
                            r"[^\n\\\"]{0,20}?(?:No such file or directory|command not found|not found)[ \t]*(?:\r?\n|\\+n)?")
_EMPTY_FIELD = re.compile(r"(?:,\s*(?P<k1>\\*\"?(?:explanation|note|notes|summary|reason|message|objective|comment)\\*\"?)"
                          r"\s*:\s*\\*\"\\*\"(?=\s*[,}])|(?<=\{)\s*(?P<k2>\\*\"?(?:explanation|note|notes|summary|reason|message|"
                          r"objective|comment)\\*\"?)\s*:\s*\\*\"\\*\"\s*,?\s*)")


# --- encoded clocks
_B64_BLOB = re.compile(r"\s*[A-Za-z0-9+/]{200,}={0,2}\s*")
_PDF_DATES = [re.compile(rb"/(?:CreationDate|ModDate)\s*\(D:[^)]*\)")]


def scrub_blob(text):
    """A base64 document (a PDF read by the agent) keeps its bytes except its creation/modification dates, blanked to
    spaces of the same length so the file stays valid. Other blobs are left as they are."""
    import base64
    core = text.strip()
    try:
        raw = base64.b64decode(core + "=" * (-len(core) % 4), validate=False)
    except ValueError:
        return text
    if not raw.startswith(b"%PDF"):
        return text
    n = 0
    for p in _PDF_DATES:
        raw, k = p.subn(lambda m: b" " * len(m.group(0)), raw)
        n += k
    if not n:
        return text
    new = base64.b64encode(raw).decode()
    return text[:len(text) - len(text.lstrip())] + new + text[len(text.rstrip()):]


_URL_PARAM = re.compile(r"(?P<sep>[?&;])(?P<k>[\w.-]{1,24})=(?P<v>[A-Za-z0-9_\-]{12,}={0,2})(?P<nxt>&?)")


def url_encoded_dates(text):
    """URL parameters holding a base64url protobuf or string with a date in it (a `ts=CAEa...` parameter whose
    varint fields encode a date) become the removal token."""
    import base64
    if "=" not in text or "?" not in text:
        return text

    def f(m):
        v = m.group("v")
        try:
            raw = base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
        except ValueError:
            return m.group(0)
        if re.search(rb"[\xe8-\xeb]\x0f|202[4-7][-/]?[01]\d", raw):
            return "?" if m.group("sep") == "?" and m.group("nxt") else "" if m.group("sep") == "?" else m.group("nxt")
        return m.group(0)
    return _URL_PARAM.sub(f, text)


# the agent's own session log grepped into a result (Codex rollout.jsonl, Claude project transcripts): harness records
_SESSION_LOG_LINE = re.compile(r"(?:(?<=^)|(?<=\n)|(?<=\\n))[^\n]*?(?:codex-home/sessions|\.codex/sessions|\.claude/projects|"
                               r"/logs/agent/sessions)/[^\s:\\]*\.jsonl:\d+:[^\n]*?(?=\\n|\n|$)(?:\r?\n|\\n)?")


def scrub_text(text, kind, ctx):
    """The pipeline for one non-prose string (tool call argument, tool output, system or other user text)."""
    if _B64_BLOB.fullmatch(text):
        return scrub_blob(text)
    blobs = []
    if len(text) > 400 and _B64_INLINE.search(text):
        # base64 documents inside a text (a PDF the agent read) are scrubbed as documents and kept out of the text rules
        def stash(m):
            blobs.append(scrub_blob(m.group(0)))
            return "\x02%d\x03" % (len(blobs) - 1)
        text = _B64_INLINE.sub(stash, text)
    text = _scrub_text(text, kind, ctx)
    if blobs:
        text = re.sub(r"\x02(\d+)\x03", lambda m: blobs[int(m.group(1))], text)
    return text


_B64_INLINE = re.compile(r"(?<=[\"'\s:])[A-Za-z0-9+/]{400,}={0,2}(?=[\"'\s,]|$)")


def _scrub_text(text, kind, ctx):
    rm, rr = ctx.req_min, ctx.req_rules
    orig_text = text
    text = url_encoded_dates(text)
    if kind == "tool_result":
        text = _SESSION_LOG_LINE.sub("", text)
    before = {text[a:b] for a, b, _ in _segments(text)}
    text = INSTRUCTION.sub("", text)
    text = _work_rewrite(text)
    text = _clause_rewrite(text)
    text = _clause2_rewrite(text)
    if kind in ("tool_call", "tool_result"):
        text = code_statements(text, ctx.loop_counts, ctx.loop_labels)
        if kind == "tool_result" and ctx.loop_counts:
            text = count_labels(text, ctx.loop_counts)
        if kind == "tool_result" and ctx.loop_labels:
            text = loop_counter_output(text, ctx.loop_labels)
        if kind == "tool_result":
            text = _TIME_TOOL_ERR.sub("", text)
        text = _BANNER_WORD.sub(lambda m: m.group(1).rstrip() + " ", text)
        text = container_blocks(text)
        text = pair_removal(text)
        time_names = set()
        text = bookkeeping_lines(text, time_names)
        text = _EMPTY_STMT.sub("", text)
    if kind in ("tool_call", "data"):
        text = re.sub(r"[ \t]*\((?:(?:too|very|rather|quite|extremely|painfully|so|really)\s+)?slow(?:er)?\)", "", text)
    text = drop_budget_sentences(text, rm, vague=(kind in ("tool_call", "data")))
    text = token_scrub(text, rr)
    if REMOVED in text:
        text = shell_trim(text, mid=kind in ("tool_call", "data"))
    if kind == "tool_call" and REMOVED in text:
        text = echo_literals(text)
    if kind in ("tool_call", "data") and REMOVED in text:
        # agent-written sentences (notes, memory files, plans) still holding a removed time value go whole
        text = drop_budget_sentences(text, rm, removed_only=True)
    if kind in ("tool_call", "tool_result"):
        text = empty_items(text)
    if kind in ("tool_call", "tool_result"):
        text = hollow_code(text, time_names, orig_text)
    if kind == "tool_call" and len(_EMPTY_FIELD.findall(text)) > len(_EMPTY_FIELD.findall(orig_text)):
        # `{explanation:"", plan:[...]}`: a field the scrub emptied goes with its key
        text = _EMPTY_FIELD.sub("", text)
    text = post_clean(text, before, max_line=160 if kind == "tool_result" else 400)
    if kind in ("tool_call", "tool_result"):
        text = hollow_writes(text, orig_text)
        text = emptied_pairs(text, orig_text)
        text = blank_runs(text, orig_text)
    return text


# --- messages
_CALL_HDR = re.compile(r"\[tool_call name=(?P<name>\S+?) id=(?P<id>\S+?)\]")
_RESULT_HDR = re.compile(r"\[tool_result id=(?P<id>\S+?)[\] ]")


def _trivial_call(body):
    b = body.strip()
    if not b:
        return True
    cmds = re.findall(r"\b(?:cmd|command)\\*\"?\s*:\s*\\*\"((?:[^\"\\]|\\.)*)\\*\"", b)
    calls = set(re.findall(r"tools\.(\w+)\(", b))
    if cmds and all(not re.search(r"[A-Za-z0-9]", c.replace("\\n", " ")) for c in cmds) and calls <= {"exec_command"}:
        return True
    # Codex JS left with no tool call at all (an empty allSettled array)
    if not calls and re.search(r"Promise\.all(?:Settled)?\(\s*\[\s*\]\s*\)", b):
        return True
    return False


# --- polls of running commands (waits in disguise)
_WS_POLL = re.compile(r"^\s*(?://[^\n]*\n\s*)*(?:(?:const|let|var)\s+\w+\s*=\s*)?(?:text\(\s*)?(?:await\s+)?"
                      r"tools\.write_stdin\(\s*\{(?P<args>[^{}]*)\}\s*\)\s*\)?\s*;?\s*"
                      r"(?:text\((?:JSON\.stringify\()?\w+(?:\.output)?\)?\)\s*;?\s*)?$")
_RESULT_WRAP = re.compile(r"(?m)^\s*(?:Script completed|Script running with cell ID \d+|Output:|Wall time[^\n]*|"
                          r"Process running with session ID \d+|Chunk ID: \w+|Original token count: \d+)\s*$")


def poll_session(name, body):
    """Session key if this tool call only polls a running command for output with no input (a wait), else None."""
    b = body.strip()
    if name == "wait":                                         # Codex code-mode `wait` on a running cell
        m = re.search(r"\"cell_id\"\s*:\s*\"?(\w+)", b)
        return ("cell", m.group(1)) if m else None
    m = _WS_POLL.match(b)
    if m and re.search(r"\bchars\\*\"?\s*:\s*\\*\"\\*\"", m.group("args")):
        sid = re.search(r"session_id\\*\"?\s*:\s*(\d+)", m.group("args"))
        return ("session", sid.group(1)) if sid else None
    return None


def result_parts(body):
    """(output text, exit status seen, session id) of a tool result; output has wrappers and bookkeeping removed."""
    texts = []
    obj = _loads(body)
    items = obj if isinstance(obj, list) else [body]
    for it in items:
        t = it.get("text", "") if isinstance(it, dict) else (it if isinstance(it, str) else "")
        texts.append(t)
    out, exited, sid = [], False, None
    for t in texts:
        inner = _loads(t)
        if isinstance(inner, dict) and ("output" in inner or "chunk_id" in inner or "session_id" in inner):
            exited = exited or "exit_code" in inner
            sid = str(inner["session_id"]) if "session_id" in inner else sid
            out.append(str(inner.get("output", "")))
            continue
        out.append(_RESULT_WRAP.sub("", t))
    return "\n".join(x for x in out if x.strip()).strip(), exited, sid


_HOLD = re.compile(r"(?i)^(?:(?:the|all|both|everything|each|these|this|it|its|my|our|every)\b[^.!?]{0,160}?\b(?:is|are|remains?|remained|stays?)"
                   r"\s+(?:now\s+|still\s+|fully\s+|already\s+|all\s+)?(?:ready|complete|completed|stable|unchanged|intact|saved|"
                   r"verified|in\s+place|consistent|done|finished|final|byte-stable|reproducible)\b|no\s+further\s+\w+\s+(?:is|are|was|were)"
                   r"\s+(?:needed|required|necessary)|no\s+(?:new|further)\s+(?:issues|changes|discrepancies|warnings|failures|errors)\b)")


_STATUS = re.compile(r"(?i)\b(?:still|remains?|remained|continues?\s+to|has\s+appeared|have\s+appeared|unchanged|identical|"
                     r"intact|stable|healthy|ready|no\s+(?:new|late|further|change)|already\s+(?:passed|verified|saved|complete))\b")


def _hold_sentence(s):
    """A status-only sentence ("All outputs are saved.", "The table is still unchanged.")."""
    s = s.strip()
    if len(s.split()) > 30 or re.search(r"`|\(|\{|\[|=", s):
        return False
    return bool(_HOLD.search(s)) or (bool(_STATUS.search(s)) and not re.search(r"\d{3,}", s))


def merge_prose_runs(api_messages, out, drop_ids):
    """Assistant prose messages that became neighbours because the wait/clock/poll pairs between them were dropped are
    merged into one message (their count was a count of waits); repeated "everything is ready" holding sentences
    after the first go."""
    def is_prose(m):
        return m["role"] == "assistant" and m["content"].strip() and not m["content"].startswith("[tool_call ")
    final = max((i for i, m in enumerate(out) if is_prose(m)), default=-1)   # the final answer stays its own message
    last, gap_dropped, run_hold = None, False, False
    for i, msg in enumerate(out):
        if not msg["content"].strip():
            h = _CALL_HDR.match(api_messages[i]["content"]) or _RESULT_HDR.match(api_messages[i]["content"])
            if h and h.group("id") in drop_ids:
                gap_dropped = True
            continue
        if is_prose(msg) and last is not None and gap_dropped and i != final:
            kept = []
            for line in msg["content"].split("\n"):
                sents = _split_sentences(line)
                keep = []
                for x in sents:
                    if _hold_sentence(x) and run_hold:
                        continue
                    if _PROGRESS_ONLY.match(x.strip()):
                        continue
                    run_hold = run_hold or _hold_sentence(x)
                    keep.append(x)
                if sents and not keep:
                    continue
                kept.append(" ".join(keep) if sents else line)
            add = "\n".join(kept).strip()
            if add:
                out[last] = {**out[last], "content": out[last]["content"].rstrip() + "\n\n" + add}
            out[i] = {**msg, "content": ""}
            gap_dropped = False
            continue
        if is_prose(msg):
            last, gap_dropped = i, False
            run_hold = any(_hold_sentence(x) for x in _split_sentences(msg["content"].replace("\n", " ")))
        else:
            last, gap_dropped, run_hold = None, False, False
    return out


# --- hold checks and poll chains
_SLEEP_ANY = re.compile(r"(?:(?<=\\n)|(?<=\\r)|(?<![\w]))sleep[ \t]+[\d.$]|\bsleep\s*\(|setTimeout\(|tools\.sleep|\bSys\.sleep")
_MEAS = re.compile(r"\$\((?:[^()]|\([^()]*\))*\)|\b(?:hashlib\.)?(?:sha256|sha1|md5|blake2b)\((?:[^()]|\([^()]*\))*\)"
                   r"(?:\.hexdigest\(\))?|\b(?:os\.stat|stat|getsize|os\.path\.getsize|getmtime)\((?:[^()]|\([^()]*\))*\)"
                   # a fingerprint command run on both sides of the sleep: `sha256sum x; sleep 9; sha256sum x`
                   r"|(?:(?<=\\n)|(?<=[;&|(]\s)|\b)(?:sha\d*sum|md5sum|cksum|b2sum|stat|wc|du|ls)[ \t][^\n;&|\\\"]{4,}")
_HOLD_WORDS = re.compile(r"(?i)stabil|quiescen|\bstable\b|unchanged|\bsettled?\b|still[_ ](?:same|match)")
_LAUNCH = re.compile(r"\bnohup\b|\bsetsid\b|\bdisown\b|(?<![&>])&(?:[ \t]*(?:\\n|\n|;|$)|[ \t]+(?!&))|\bPopen\b|\bsubprocess\b|"
                     r"\bpip\b|\bapt(?:-get)?\b|\bRscript\b|\bjupyter\b|\bnbconvert\b|\bpython3? [\w./-]+\.py\b")


_READONLY_POLL = re.compile(r"\b(?:tail|head|cat|grep|egrep|rg|ls|ps|pgrep|wc|stat|free|du|df|nvidia-smi|awk|sed\s+-n)\b")
_WRITES = re.compile(r"(?<![0-9&>])>{1,2}[ \t]*(?!/dev/null|&)[\w/.$\"'~]|\b(?:rm|mv|cp|mkdir|touch|kill|pkill|python\d?(?:\.\d+)?|"
                     r"Rscript|R|make|pip\d?|apt|git|tee|chmod|curl|wget|apply_patch|sed\s+-i)\b|tools\.(?!exec_command|write_stdin)\w+")


def hold_check(orig_body):
    """A call that measures the same thing on both sides of a sleep, or calls itself a stability/quiescence check around
    a sleep, and launches nothing: a wait dressed as a check (its sleep is gone, so it would read as a doubled hash)."""
    if not _SLEEP_ANY.search(orig_body) or _LAUNCH.search(orig_body):
        return False
    if _HOLD_WORDS.search(orig_body):
        return True
    for sm in _SLEEP_ANY.finditer(orig_body):
        a, b = orig_body[:sm.start()], orig_body[sm.end():]
        if any(len(x.group(0)) >= 12 and x.group(0) in b for x in _MEAS.finditer(a)):
            return True
    # a fingerprint taken before a sleeping loop and re-taken inside it
    return any(len(x.group(0)) >= 12 and orig_body.count(x.group(0)) >= 2 for x in _MEAS.finditer(orig_body))


def _stream_item(body):
    """(items, index, prefix, obj) for a Codex exec/poll result whose text item holds {"output": ...}; None otherwise."""
    items = _loads(body)
    if not isinstance(items, list):
        return None
    for k, it in enumerate(items):
        t = it.get("text") if isinstance(it, dict) else None
        if not isinstance(t, str) or "{" not in t:
            continue
        j = t.find("{")
        try:
            obj, end = json.JSONDecoder().raw_decode(t[j:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "output" in obj and not t[j + end:].strip():
            return items, k, t[:j], obj
    return None


def collapse_poll_chains(out, by_id, drop_ids):
    """A run of polls of one running command with only prose between them is one wait: the first poll keeps the
    chain's whole output (and the last poll's exit status), the later polls go. A command repeated verbatim with only
    prose between (a hand-run poll: `tail -n 2 out.log`) keeps its last run."""
    live = sorted((idx[0], idx[1], tid) for tid, idx in by_id.items() if len(idx) == 2 and tid not in drop_ids)

    def key_of(ci):
        hdr, _, body = out[ci]["content"].partition("\n")
        m = _CALL_HDR.match(hdr)
        k = poll_session(m.group("name") if m else "", body)
        if k:
            return k
        if not _READONLY_POLL.search(body) or _LAUNCH.search(body) or _WRITES.search(body):
            return ("once", ci)                            # a command with effects is never a poll
        return ("cmd", (m.group("name") if m else ""), body.strip())

    def only_prose(a, b):
        return all(not out[x]["content"].strip() or (out[x]["role"] == "assistant"
                                                     and not out[x]["content"].startswith("[tool_call ")) for x in range(a, b))

    def flush(chain):
        if len(chain) < 2:
            return
        if chain[0][3][0] == "cmd":                       # repeated command: the last run stays
            for ci, ri, tid, _ in chain[:-1]:
                drop_ids.add(tid)
                out[ci] = {**out[ci], "content": ""}
                out[ri] = {**out[ri], "content": ""}
            return
        parsed = [_stream_item(out[ri]["content"].partition("\n")[2]) for _, ri, _, _ in chain]
        if any(p is None for p in parsed):                 # an unreadable (truncated) poll splits the chain
            k0 = parsed.index(None)
            flush(chain[:k0])
            flush(chain[k0 + 1:])
            return
        items, k, prefix, first = parsed[0]
        merged = dict(first)
        merged["output"] = "".join(str(p[3].get("output", "")) for p in parsed)
        if isinstance(first.get("original_token_count"), int):
            merged["original_token_count"] = sum(p[3].get("original_token_count", 0) or 0 for p in parsed
                                                 if isinstance(p[3].get("original_token_count", 0), int))
        last = parsed[-1][3]
        if "exit_code" in last:
            merged["exit_code"] = last["exit_code"]
            merged.pop("session_id", None)
        items = list(items)
        items[k] = {**items[k], "text": prefix + _dumps(merged, items[k]["text"][len(prefix):])}
        hdr0 = out[chain[0][1]]["content"].partition("\n")[0]
        out[chain[0][1]] = {**out[chain[0][1]], "content": hdr0 + "\n" + _dumps(items, out[chain[0][1]]["content"].partition("\n")[2])}
        for ci, ri, tid, _ in chain[1:]:
            drop_ids.add(tid)
            out[ci] = {**out[ci], "content": ""}
            out[ri] = {**out[ri], "content": ""}

    chain, prev_ri = [], None
    for ci, ri, tid in live:
        key = key_of(ci)
        if chain and chain[-1][3] == key and only_prose(prev_ri + 1, ci):
            chain.append((ci, ri, tid, key))
        else:
            flush(chain)
            chain = [(ci, ri, tid, key)]
        prev_ri = ri
    flush(chain)
    return out


# a progress-only status line in narration merged across dropped polls ("The sweep is on step 4.")
_PROGRESS_ONLY = re.compile(r"(?i)^(?:the|its|this|that|both)\b[^.;:!?]{0,80}?\b(?:is|are)\s+(?:now\s+|still\s+)?(?:on|at|in)\s+"
                            r"(?:[\w-]+\s+){0,3}?(?:epoch|step|iteration|cell|stage|phase|batch|round|chunk|file)\s+\d+"
                            r"(?:\s+of\s+\d+)?\s*[.!]?$")


_TOOL_TALK = re.compile(r"(?i)\btool[- ]?(?:calls?|results?|use|requests?|outputs?)\b|\bprivately\s+listed\s+needs\b|"
                        r"\bitems?\s+to\s+request\b|\badditional\s+requests\b")


def _drop_lines(text, pattern):
    """Lines with a sentence that matches `pattern` go."""
    lines = [x for x in text.split("\n") if not any(pattern.search(s) for s in _split_sentences(x))]
    return "\n".join(lines).strip("\n")


def scrub_messages(api_messages, req_min, year):
    """The scrubbed messages, in order and with their roles; emptied messages are kept as "" for the caller to
    drop."""
    req_rules = [session_year_rule(year)]
    ctx = Ctx(req_min, req_rules)
    # clock and sleep tools: the call and its result go as a pair
    drop_ids = set()
    for msg in api_messages:
        m = _CALL_HDR.match(msg["content"])
        if m and CLOCK_TOOL.match(m.group("name")):
            drop_ids.add(m.group("id"))
    out = []
    ps_calls = {}
    proc_calls = set()
    listing_calls = set()                              # calls whose output lists file/process times (ps, stat, ls -l)
    settled_gone = {}                                  # call id -> positions of emptied Promise.allSettled items
    last_call_name = ""
    for msg in api_messages:
        role, content = msg["role"], msg["content"]
        header, body = "", content
        kind = "prose"
        if role == "assistant" and content.startswith("[tool_call "):
            kind = "tool_call"
        elif content.startswith("[tool_result "):
            kind = "tool_result"
        elif role == "system":
            kind = "system"
        if kind in ("tool_call", "tool_result"):
            mid = (_CALL_HDR if kind == "tool_call" else _RESULT_HDR).match(content)
            if mid and mid.group("id") in drop_ids:
                out.append({**msg, "content": ""})
                continue
            nl = content.find("\n")
            header, body = (content, "") if nl < 0 else (content[:nl + 1], content[nl + 1:])
        # 1. the duration instruction (anywhere it appears, e.g. echoed by a tool)
        body, n = INSTRUCTION.subn("", body)
        if n:
            if kind == "prose" and role == "user":
                kind = "task_prompt"
        if kind == "tool_call":
            prof = ps_profile(body)
            mid = re.search(r"\bid=(\S+?)\]", header)
            if prof is not None and mid:
                ps_calls[mid.group(1)] = prof
            if mid and _PROC_STAT_CALL.search(body):
                proc_calls.add(mid.group(1))
            if mid and _CLOCK_READ_CALL.search(_escape_view(body)):
                listing_calls.add(mid.group(1))
            last_call_name = re.search(r"name=(\S+?)[\] ]", header).group(1) if "name=" in header else ""
        elif kind == "tool_result":
            mid = re.search(r"\bid=(\S+?)\]", header)
            prof = ps_calls.get(mid.group(1)) if mid else None
            if prof is None and ps_calls and last_call_name in _POLL_TOOLS:
                prof = set().union(*ps_calls.values())     # output of an earlier ps call arriving through a poll
            if prof is not None:
                body = ps_scrub(body, prof)
            if mid and (mid.group(1) in proc_calls or (proc_calls and last_call_name in _POLL_TOOLS)):
                body = proc_stat_scrub(body)
            body = lone_time_segments(body)
        # 2. kind-specific scrubbing
        if kind == "task_prompt":
            body = token_scrub(body, [], clock_only=True)
            body = body.rstrip()
        elif kind == "prose" and role == "assistant":
            body = scrub_prose(body, ctx)
        elif kind == "prose":                              # other user text (env context, plugin lists, images)
            body = scrub_text(body, "user", ctx)
        else:                                              # tool_call, tool_result, system
            new = scrub_struct(body, kind, ctx)
            if new != body:
                new = re.sub(r"\A(?:[ \t]*\n)+", "", new)
            body = new
            if kind == "tool_call" and mid:
                body, gone = prune_settled_array(body)
                if gone:
                    settled_gone[mid.group(1)] = gone
            elif kind == "tool_result" and mid and mid.group(1) in settled_gone:
                body = prune_settled_result(body, settled_gone[mid.group(1)])
            if kind == "tool_result" and mid and mid.group(1) in listing_calls:
                body = removed_columns(body)
        out.append({**msg, "content": header + body})
    # a call whose arguments were nothing but time content (e.g. `sleep 600`) goes with its result
    wait_sessions = set()
    by_id = {}
    for i, msg in enumerate(out):
        c = msg["content"]
        m = _CALL_HDR.match(c) if msg["role"] == "assistant" else _RESULT_HDR.match(c)
        if m and m.group("id") not in drop_ids:
            by_id.setdefault(m.group("id"), []).append(i)
    for tid, idx in by_id.items():
        if len(idx) != 2:
            continue
        ci, ri = idx
        cbody = out[ci]["content"].split("\n", 1)[1] if "\n" in out[ci]["content"] else ""
        rbody = out[ri]["content"].split("\n", 1)[1] if "\n" in out[ri]["content"] else ""
        orig_c = api_messages[ci]["content"].split("\n", 1)[1] if "\n" in api_messages[ci]["content"] else ""
        if orig_c.strip() and _trivial_call(cbody) and not _trivial_call(orig_c):
            # the whole command was time content (`sleep 50`): the pair goes even when the result names the session
            # the sleep ran in; later polls of that session are waits too
            drop_ids.add(tid)
            _, _, sid = result_parts(rbody)
            if sid:
                wait_sessions.add(("session", sid))
            out[ci] = {**out[ci], "content": ""}
            out[ri] = {**out[ri], "content": ""}
            continue
        if hold_check(orig_c):
            # a stability/quiescence check around a sleep (hash, sleep, hash again): a wait; the pair goes, and later
            # polls of the session it ran in are waits too
            drop_ids.add(tid)
            _, _, sid = result_parts(rbody)
            if sid:
                wait_sessions.add(("session", sid))
            out[ci] = {**out[ci], "content": ""}
            out[ri] = {**out[ri], "content": ""}
    # polls with nothing new (an empty read of a running command), polls of a sleep's session, and polls that only
    # repeat the previous poll's output (a monitor loop re-printing the same line) are waits: the pair goes
    last_out = {}
    for tid, idx in sorted(by_id.items(), key=lambda kv: kv[1][0]):
        if len(idx) != 2 or tid in drop_ids:
            continue
        ci, ri = idx
        chdr, _, cbody = out[ci]["content"].partition("\n")
        rbody = out[ri]["content"].partition("\n")[2]
        name = _CALL_HDR.match(chdr).group("name") if _CALL_HDR.match(chdr) else ""
        output, exited, sid = result_parts(rbody)
        key = poll_session(name, cbody)
        if key is None:
            if sid and output:
                last_out[("session", sid)] = output
            continue
        why = None
        if key in wait_sessions:
            why = "poll_of_sleep_session"
        elif not output and not exited:
            why = "empty_poll"
        elif output and last_out.get(key) == output:
            why = "repeated_poll_output"
        if output:
            last_out[key] = output
        if why:
            drop_ids.add(tid)
            out[ci] = {**out[ci], "content": ""}
            out[ri] = {**out[ri], "content": ""}
    out = collapse_poll_chains(out, by_id, drop_ids)
    out = merge_prose_runs(api_messages, out, drop_ids)
    # when every tool call in the session was a clock or sleep call, talk about tool calls refers to the clock
    call_ids = [m.group("id") for m in (_CALL_HDR.match(x["content"]) for x in api_messages) if m]
    if call_ids and all(i in drop_ids for i in call_ids):
        for i, msg in enumerate(out):
            if msg["role"] == "assistant" and msg["content"] and not msg["content"].startswith("[tool_call "):
                out[i] = {**msg, "content": _drop_lines(msg["content"], _TOOL_TALK)}
    return out
