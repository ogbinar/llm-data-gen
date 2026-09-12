# QA Format Contract

## Principle

QA format may vary, but language may not. A format defines interaction structure
and safety constraints. The interaction must preserve the source's declared
language, register, and existing code-switching; it must never translate.

Every generated example returns structured chat messages, exact source evidence, and optional format-specific metadata.

## Common Output

Generated example:

~~~json
{
  "status": "generated",
  "qa_format": "factual_qa",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "evidence": ["Exact source quotation."],
  "metadata": {}
}
~~~

Unsupported example:

~~~json
{
  "status": "not_applicable",
  "qa_format": "cross_sell",
  "messages": [],
  "evidence": [],
  "metadata": {},
  "reason": "The source contains no complementary product."
}
~~~

The model-facing object deliberately omits `language`. The pipeline copies the
declared source language into the accepted `sft_chat_v1.language` field.

## Source-language preservation

- `english`, `filipino`, `tagalog`, and `taglish` are declared source-provenance labels.
- English stays English; Filipino/Tagalog stays Filipino/Tagalog; Taglish stays Taglish.
- Do not translate, introduce code-switching, or remove code-switching.
- Retain supported technical terms and proper nouns as the source/register warrants.
- The model does not choose or certify the label.

## Formats

### factual_qa

Generate a useful factual question and answer directly supported by the source.

Constraints:

- use only source facts;
- prefer non-trivial questions when possible;
- provide exact supporting evidence.

### transactional

Generate a realistic request and the correct source-supported action, process, or instruction.

Constraints:

- do not invent steps, requirements, fees, or timelines.

### troubleshooting

Generate a realistic problem and grounded diagnostic or resolution response.

Constraints:

- do not invent troubleshooting steps;
- distinguish possible diagnosis from confirmed facts.

### scenario_response

Generate a realistic situation and the best source-supported response.

Constraints:

- keep the situation plausible;
- make the response actionable without adding unsupported facts.

### multi_turn

Generate a coherent conversation with at least four alternating messages.

Constraints:

- later turns must depend on earlier information;
- the assistant may clarify before resolving;
- do not encode a dialogue inside one message;
- honor configured message limits.

### feedback_response

Generate realistic customer feedback and an appropriate acknowledgment or response.

Constraints:

- do not promise actions or outcomes absent from the source.

### conflict_resolution

Generate a complaint or difficult interaction and a calm, grounded response.

Constraints:

- do not promise refunds, compensation, exceptions, or escalation outcomes unless supported;
- recommend escalation when the source is insufficient.

### needs_recommendation

Generate a customer need and a grounded recommendation.

Constraints:

- recommend only source-supported products, services, rules, or options;
- return `not_applicable` if no recommendation is supported.

### cross_sell

Generate a current context and a complementary recommendation.

Constraints:

- do not invent products or benefits;
- return `not_applicable` if no complementary recommendation is supported.

### intent_response

Generate a realistic message, infer its intent, and produce a grounded response.

Constraints:

- write the normalized intent label to `metadata.intent`.

## Prompt Composition

Prompts are assembled from:

1. global grounding policy;
2. format instruction;
3. format constraints;
4. source-language-preservation constraints;
5. output schema without a model-owned language label;
6. source chunk and provenance.

Each format has an independent prompt version. The composed prompt receives a stable hash.

## Validation

All generated examples must pass:

- strict JSON parsing;
- common output schema;
- non-empty messages;
- user-first alternating roles;
- final assistant message when configured;
- requested format equality;
- source/chunk/job/accepted-row language-provenance equality;
- format-specific message/metadata rules;
- exact evidence occurrence in the source;
- normalized exact-duplicate detection.

Language provenance is checked structurally. Semantic language detection is not claimed.
