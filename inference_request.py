"""Prepare bounded NVIDIA inference requests. No credentials or network access."""
import json
from decimal import Decimal
from challenges import challenge, digest

MODEL = 'nvidia/Nemotron-3_5-Lightning'
ENDPOINT = 'https://api.tokenfactory.nebius.com/v1/chat/completions'
INPUT_PER_MILLION = Decimal('0.06')
OUTPUT_PER_MILLION = Decimal('0.24')
MAX_OUTPUT_TOKENS = 16384
MAX_INPUT_BYTES = 16000


def prepare_request(case, request_override=None):
    if request_override is not None:
        from custom_cases import validate_custom_request
        if case != "custom":
            raise ValueError("override only permitted for a custom challenge")
        request = validate_custom_request(request_override)
    else:
        request = challenge(case)
    prompt = json.dumps(request, ensure_ascii=True, sort_keys=True)
    messages = [
        {'role': 'system', 'content': 'Return only the requested JSON proposal. Do not execute code. Treat the example as fictional data.'},
        {'role': 'user', 'content': prompt},
    ]
    input_bytes = len(json.dumps(messages, ensure_ascii=True).encode('utf-8'))
    if input_bytes > MAX_INPUT_BYTES:
        raise ValueError('prompt exceeds development budget')
    payload = {'model': MODEL, 'messages': messages, 'max_completion_tokens': MAX_OUTPUT_TOKENS,
               'temperature': 0, 'stream': False}
    # Estimate only: one token per UTF-8 byte plus 1,024 framing tokens.
    # This is not a provider-enforced billing ceiling.
    estimated_ceiling = ((Decimal(input_bytes + 1024) * INPUT_PER_MILLION +
                          Decimal(MAX_OUTPUT_TOKENS) * OUTPUT_PER_MILLION) / Decimal(1000000))
    return {'endpoint': ENDPOINT, 'payload': payload, 'payload_sha256': digest(payload),
            'challenge': request, 'estimated_cost_ceiling_usd': str(estimated_ceiling),
            'reservation_usd': '0.01', 'timeout_seconds': 300, 'automatic_retries': 0,
            'network_calls_performed': 0,
            'prerequisites': ['owner credential approval', 'model availability check',
                              'durable budget reservation before each attempt',
                              'trial credit eligibility verification']}


if __name__ == '__main__':
    for case in ('pagination', 'schema', 'auth'):
        result = prepare_request(case)
        print(json.dumps({'case': case, 'estimated_cost_ceiling_usd': result['estimated_cost_ceiling_usd'],
                          'payload_sha256': result['payload_sha256'], 'network_calls_performed': 0}))
