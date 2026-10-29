# Understanding the Messages API: How Your Code Talks to an AI Model

When building an AI agent, two separate systems work together:
1. **The Model:** The AI itself (such as Claude), hosted on the provider's servers.
2. **The Harness (Orchestrator):** The application code you write to run the agent, manage its conversation history, and execute its tools. While the language model provides reasoning, the harness acts as the orchestrator - the central control system handling memory, error recovery, and control flow. An agent is defined as: **Agent = LLM + Tools + Orchestrator**. Even a single-agent system requires this orchestrator to run the execution loop.

This guide explains how the harness and the model communicate using Anthropic's Messages API (and its Python SDK). While the examples here use Anthropic's naming conventions, almost all modern LLM APIs follow this exact same architecture.

*(To see how our specific guardian agent implements these concepts, see The Agent Loop in Action.)*

## 1. The Big Picture: A Stateless Conversation Loop

At its core, talking to a model requires a single HTTP request: `POST /v1/messages`. The model has no memory between requests. Once it sends you an answer, it forgets everything. Because of this, your harness is responsible for keeping track of the conversation. Every time your harness calls the API, it must send the entire conversation history from the very beginning, along with the system instructions, configuration settings, and available tools.

This Agentic Loop follows a strict five-step execution sequence:
1. **Tool Discovery:** The harness queries the framework to retrieve the JSON schemas defining available tools.
2. **Tool Binding (Context Provisioning):** The harness injects the tool schemas into the API request alongside the user's prompt.
3. **Action Selection (Function Calling):** The model pauses text generation and outputs a structured payload containing the exact tool name and arguments.
4. **Action Execution (Tool Invocation):** The harness parses the structured request and executes the local code.
5. **Observation Feedback:** The harness appends the tool results back into the conversation history and sends the updated thread back to the model.

## 2. Conversation Roles: Who Is Speaking?

Every message in the `messages` list is tagged with a `role`. This tells the API which side of the conversation produced that message.

* **`user`:** The outside world (either a human or your harness program). A human's question, the initial task description from the harness, or the results of a tool that just ran.
* **`assistant`:** The AI model itself. The model's previous responses, which your harness sends back unchanged to maintain history.
* **`system`:** The application operator. Mid-conversation instructions injected during a long run.

**Key Mental Models for Roles:**
* **"User" does not mean "Human":** The model never actually knows whether it is talking to a human being or an automated computer program - and it doesn't need to. In the API's vocabulary, `user` simply means "input coming from outside the model."
* **Tool results travel inside `user` messages:** When the model asks to run a tool, your harness runs it and sends the output back inside a `user` message.
* **Turns alternate:** Conversations strictly alternate between `user` and `assistant` messages.

## 3. Inside a Message: Content Blocks

Rather than holding a plain string, a message's `content` is a list of blocks. Each block has a `type` field that defines what kind of data it holds and how it should be handled.

**Blocks the Model Sends to Your Harness:**
* **`text`:** Natural language meant to be read.
* **`thinking`:** The model's internal reasoning step.
* **`tool_use`:** A formal request from the model asking your harness to execute one of your tools. Contains a unique `id`, the tool `name`, and the `input` arguments.
* **`server_tool_use`:** A built-in tool executed directly by the provider's server, immediately followed by its result.

**Blocks Your Harness Sends to the Model:**
* **`text`:** Plain text input, such as task instructions.
* **`tool_result`:** The output from a tool your harness just executed. It must include a `tool_use_id` that matches the exact `id` of the model's `tool_use` request. If the tool failed, set `is_error: true`.

## 4. Extraction & Checking Why the Model Stopped: `stop_reason`

Before your harness looks at the content blocks in a response, it must check `stop_reason`. Modern models do not rely on text parsing (Regex) to extract commands. Instead, the harness reads strictly formatted JSON fields.

* **`tool_use`:** The model paused because it wants your harness to run a tool. The harness extracts the tool name and arguments directly from the JSON payload, executes the local code, and appends the `tool_result` blocks in a new `user` message.
* **`end_turn`:** The model has completely finished its task. Extract the `text` blocks - this is the model's final answer.
* **`max_tokens`:** The response hit the token limit and was cut off mid-sentence.
* **`refusal`:** The model declined to fulfill the request.
* **`model_context_window_exceeded`:** The total conversation history has grown too large for the model's maximum memory limit.

## 5. The Golden Rules: How the Harness Processes a Response

* **Always inspect `stop_reason` first:** Never assume a response is complete.
* **Never execute commands found in `text` or `thinking` blocks:** Your harness must only trigger actions when it receives an explicit `tool_use` block.
* **Treat `tool_use` blocks as untrusted requests:** Validate that the tool `name` is on an approved allow-list and verify `input` parameters are safe before executing code.
* **Match every `tool_use` with a `tool_result`:** If the model requests three tools, your harness must return three `tool_result` blocks bundled inside a single `user` message.
* **Preserve the model's history without editing it:** Include all blocks - including `thinking` blocks - completely unchanged.

## 6. Under the Hood: How Text Becomes JSON & Syntax Structure

How does a language model know the exact syntax and structure to output for a JSON `tool_use`?

1. **The JSON Schema Blueprint:** The API request contains detailed tool schemas that act as rigid formatting templates defining expected variables.
2. **Under-the-Hood Instructions:** Provider APIs translate your schemas into strict system commands secretly injected into the model's prompt.
3. **Function-Calling Fine-Tuning:** Models are explicitly trained on millions of examples to instinctively switch from conversational text to structured JSON when evaluating a schema.
4. **Server Parsing:** As the model generates tokens, the provider's server intercepts special markers and packages the output into clean JSON content blocks before sending the HTTP response.
5. **Constrained Decoding (`strict: true`):** If you set `strict: true` on a tool definition, the provider's server actively restricts token generation, guaranteeing that `tool_use.input` matches your JSON schema 100% of the time.

## 7. Testing Environments: Desktop Apps vs. API Agents

Pasting agent instructions or tool schemas into a consumer desktop application (like ChatGPT or Claude) will not replicate an agentic workflow because:

* **Role Confusion:** Desktop apps treat all inputs as `user` prompts, overriding strict `system` rules.
* **Invisible Prompts:** Providers inject default hidden system prompts that conflict with your custom instructions.
* **Missing Orchestrator:** Desktop apps lack the local harness to catch a structured payload and execute code. Instead of pausing, they hallucinate outputs.
* **Fixed Randomness (Temperature):** Desktop apps use higher temperatures for conversational creativity, whereas API agents often run deterministically at a temperature of 0.

## 8. Python SDK Note: Standard vs. Beta Types

* The standard method (`client.messages.create`) returns a `Message` object containing standard blocks (`TextBlock`, `ToolUseBlock`, etc.).
* The beta method (`client.beta.messages.create`) returns a `BetaMessage` object containing beta blocks (`BetaTextBlock`, `BetaToolUseBlock`, etc.), unlocking extra fields for experimental server features.