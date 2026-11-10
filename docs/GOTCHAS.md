# Gotchas found while building this

Things that silently break image handling or the guardrail verdicts in NeMo Guardrails with a
vision model. Found with `nemoguardrails` 0.24.1 and Ollama 0.40.15 locally, and on a Red Hat
OpenShift AI (RHOAI) 3.6 EA2 cluster with a vLLM-served Qwen3-VL-8B-Instruct. Back to the [README](../README.md).

1. **Keep the judge's user message a bare `"{{ user_input }}"`.** The library only
   preserves the image when the whole message is a single variable; embedding it
   in other text stringifies it and the judge never sees the image.
2. **The main model does not see images by default.** The built-in `general` prompt
   uses `user_assistant_sequence`, which turns images into the text `[+ image]`.
   `prompts.yml` overrides `general` with `{{ history | to_chat_messages }}`.
   (The tutorial's "safe image reasoning" step does not work without this.)
3. **Thinking models + small `max_tokens` fail closed silently.** qwen3-vl used ~1360
   tokens on a trivial yes/no question. With `max_tokens: 200` it returned empty
   content and the rail blocked everything, so correct-looking BLOCKs were not
   detections. Use `max_tokens: 3000`; watch for
   `LLM returned empty content with finish_reason='length'`.
4. **You cannot turn thinking off through Ollama's `/v1` endpoint.** `reasoning_effort`
   (none/low), top-level `think: false`, and `reasoning: {effort: none}` were all
   ignored (identical token counts, reasoning still returned). NeMo forwards extra
   `parameters:` keys into the request body unchanged, so sending them is harmless,
   just ineffective here. Options: a non-thinking tag such as `qwen3-vl:8b-instruct`
   (listed on ollama.com; not yet tested here), or keep thinking and pay the latency.
5. **`rails.explain()` accumulates across requests.** Index from the call count
   before each request, not `llm_calls[0]`.
6. **Prompt wording matters.** Without "only legible values count; labels and
   blacked-out fields are not PII", gemma3 flagged field labels.
7. **Prompt-injection judges need a "who is it addressed to" rule.** Otherwise a recipe
   or a textbook code listing looks like instructions. (Both models handled these
   benign controls correctly; gemma3 still printed a stray `safe I3`.)
8. **The judge sees the user's text too, so "unrelated to the user's request" is a trap.**
   With the UI's default message "Process this document and respond.", the original I3
   ("a task unrelated to the user's request") let the Fibonacci image through 3/3: writing code
   looked related to "process this document". I3 now says a generic request never authorizes
   tasks written inside the image, and the prompt says the user's text never makes an
   in-image instruction safe. After the change: 27/27 (5 injection + 4 benign images x 3 user
   messages), and the Fibonacci image blocked 6/6 with both rails on.
