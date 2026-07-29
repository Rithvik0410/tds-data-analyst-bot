"""
The agent: a ReAct-style tool-calling loop over Groq's OpenAI-compatible
API, followed by a strict "extract just the answer value" step so we never
trust the LLM to hand-format the final Telegram reply -- the code builds
that JSON itself.
"""

import os
import json
from groq import Groq

from tools.web import web_search, web_fetch
from tools.python_exec import python_exec

MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_TOOL_STEPS = int(os.environ.get("MAX_TOOL_STEPS", "8"))

client = Groq(api_key=os.environ["GROQ_API_KEY"])

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web (DuckDuckGo) for pages relevant to a query. "
            "Use this to locate MOSPI/data.gov.in pages, dataset URLs, "
            "or general facts you need.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and return its cleaned text content "
            "(for HTML pages), or a note telling you to load it "
            "via python_exec if it's a CSV/XLS/XLSX data file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": "Run Python code (pandas/numpy available, has network "
            "access) to compute an answer from data. ALWAYS print() "
            "the results you need -- only stdout is returned to you. "
            "Use this for any arithmetic, aggregation, sorting, or "
            "loading CSV/XLSX data directly from a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                },
                "required": ["code"],
            },
        },
    },
]

_TOOL_IMPLS = {
    "web_search": lambda args: web_search(args["query"]),
    "web_fetch": lambda args: web_fetch(args["url"]),
    "python_exec": lambda args: python_exec(args["code"]),
}

SYSTEM_PROMPT = """\
You are a rigorous data analyst agent. You answer questions about public \
datasets (MOSPI, data.gov.in, and similar Indian government / general \
public data sources), and general data-analysis questions that may embed \
data directly in the question text.

Rules:
- Never guess numbers you could compute. Use python_exec with pandas to \
  actually load and compute from real data whenever a dataset is \
  referenced or linked.
- Use web_search to locate the current URL of a dataset before fetching it \
  -- government site URLs move around and change format over time.
- If data is embedded directly in the question text, you usually don't \
  need any tools -- just compute the answer (still use python_exec for any \
  nontrivial arithmetic to avoid mistakes).
- Some questions are the last message in a short multi-turn thread -- \
  earlier messages give context; answer only the final question, using \
  that context.
- When you have a confident final answer, respond with plain text (no \
  tool call) describing the answer clearly, including exact values/names. \
  Do not include any JSON formatting yourself here -- a separate step will \
  package your answer into the required JSON shape.
"""

FORMAT_SYSTEM_PROMPT = """\
You convert a solved data-analysis answer into a single JSON *value* -- \
specifically, whatever goes in place of the placeholder inside the \
"answer" key of the reply template shown in the question. \
Output ONLY that inner value. Never output the outer reply wrapper -- \
never include an "answer" key or a "log_url" key in your output, those \
belong to code outside you, not to your response.

Example:
Question contains this template: \
{"answer": {"state": "<state name>"}, "log_url": "<url>"}
Solved answer: "The state with the highest rate is Assam."
Correct output: {"state": "Assam"}
WRONG output: {"answer": {"state": "Assam"}, "log_url": ""}
WRONG output: {"state": "Assam", "log_url": ""}

Another example:
Question contains this template: \
{"answer": <number>, "log_url": "<url>"}
Solved answer: "The total is 250."
Correct output: 250
WRONG output: {"answer": 250, "log_url": ""}

Output ONLY the JSON value itself, no markdown fences, no explanation, \
nothing else. If the question doesn't specify an explicit shape, use the \
simplest reasonable JSON representation of the answer (a string, number, \
or small object) -- still just the bare value, never wrapped in "answer"/\
"log_url" keys.
"""


def _run_tool_loop(messages: list[dict], rlog) -> str:
    """Runs the ReAct loop until the model stops calling tools or we hit
    the step cap. Returns the final assistant text answer."""
    for step in range(MAX_TOOL_STEPS):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS_SPEC,
            tool_choice="auto",
            temperature=0,
        )
        msg = resp.choices[0].message
        tool_calls = msg.tool_calls or []

        rlog.log(
            "assistant_step",
            step=step,
            content=msg.content,
            has_tool_calls=bool(tool_calls),
        )

        if not tool_calls:
            return msg.content or ""

        # Append the assistant message (with tool_calls) then each tool result.
        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            rlog.log("tool_call", name=name, args=args)
            try:
                result = _TOOL_IMPLS[name](args)
            except Exception as e:  # noqa: BLE001 - surface any tool error to the model
                result = {"error": str(e)}
            rlog.log("tool_result", name=name, result=result)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str)[:8000],
                }
            )

    rlog.log("max_steps_reached")
    return "Could not reach a final answer within the tool-call budget."


def _format_final_answer(question: str, raw_answer: str, rlog):
    """Forces the free-form answer into the exact JSON value the question
    asked for, via a constrained follow-up call. Returns a parsed Python
    object (str/number/dict/list)."""
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": FORMAT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Original question (note the requested JSON shape):\n{question}\n\n"
                    f"Solved answer to package:\n{raw_answer}"
                ),
            },
        ],
        temperature=0,
    )
    text = (resp.choices[0].message.content or "").strip()
    # Strip accidental markdown fences just in case.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    rlog.log("format_step_raw", text=text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to returning the raw string -- still valid JSON as a string value.
        rlog.log("format_step_fallback_to_string")
        return text

    parsed = _unwrap_if_echoed_template(parsed, rlog)
    return parsed


def _unwrap_if_echoed_template(value, rlog):
    """Safety net: if the model echoed the whole reply wrapper (an object
    containing an "answer" key, and either a "log_url" key or nothing
    else useful), unwrap it to just the inner answer value. Recurses in
    case of double-wrapping."""
    seen_unwrap = False
    while (
        isinstance(value, dict)
        and "answer" in value
        and (set(value.keys()) <= {"answer", "log_url"})
    ):
        rlog.log("format_step_unwrapped_echoed_wrapper", was=value)
        value = value["answer"]
        seen_unwrap = True
    if seen_unwrap:
        rlog.log("format_step_unwrap_result", value=value)
    return value


def answer_question(history: list[dict], rlog) -> object:
    """
    history: list of {"role": "user", "content": ...} for the conversation
             so far, ending with the question to answer.
    Returns: a JSON-serializable Python object for the "answer" field.
    """
    latest_question = history[-1]["content"]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    raw_answer = _run_tool_loop(messages, rlog)
    rlog.log("raw_answer", text=raw_answer)

    final_value = _format_final_answer(latest_question, raw_answer, rlog)
    rlog.log("final_value", value=final_value)
    return final_value
