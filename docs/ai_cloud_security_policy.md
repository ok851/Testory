# AI Cloud Security Policy (Mandatory)

## Iron Rule

Any content sent to cloud LLM services must pass through full automatic desensitization.

- No exception for error analysis.
- No exception for script repair.
- No exception for troubleshooting payloads.

## Sensitive Data Scope

The desensitizer must mask all of the following:

- Intranet IPs
- Internal domains and URLs
- Business fields and identifiers
- Credentials such as account/password/token/cookie
- Sensitive DOM attributes and values

## Placeholder Strategy

Raw values must be replaced by meaningless placeholders:

- `http://192.168.1.100/oa/order` -> `BUSINESS_SYSTEM_001`
- `test_user@example.com` -> `ACCOUNT_001`
- `<input id="orderNo" value="A123">` -> `<input id="DOM_FIELD_001" value="DOM_FIELD_002">`

## Implementation Baseline

Project baseline implementation is provided in:

- `cloud_desensitizer.py`
- `cloud_llm_gateway.py`

Any new cloud AI integration must call `CloudLLMGateway.call()` instead of direct HTTP SDK usage.

## Compliance

- Placeholder mapping is local only, never uploaded to cloud.
- Cloud requests must include `X-Desensitized: true`.
- If payload appears sensitive and unchanged after sanitization, request must be blocked.
