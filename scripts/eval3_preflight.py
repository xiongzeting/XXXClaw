"""Read-only, secret-free evaluation preflight."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from MiniClaw.llm.env_file import merged_environment, read_env_file
from MiniClaw.llm.config import load_llm_settings
env = merged_environment(read_env_file(ROOT / '.env') if (ROOT / '.env').is_file() else {})
settings = load_llm_settings(provider='primary', model_id='gpt-5.6-luna', environment=env)
print(json.dumps({
    'model': settings.model_id, 'credential_present': bool(settings.api_key),
    'base_url': settings.base_url, 'fallback_count': len(settings.fallbacks),
    'input_per_million': settings.input_cost_per_million,
    'cached_input_per_million': settings.cached_input_cost_per_million,
    'output_per_million': settings.output_cost_per_million,
    'timeout': settings.timeout_seconds, 'max_retries': settings.max_retries,
    'context_window': settings.context_window,
}, ensure_ascii=False, indent=2))
if '--pricing' in sys.argv:
    import httpx
    out = ROOT / '.aster/evals/eval3-hardened-r1/preflight'
    out.mkdir(parents=True, exist_ok=True)
    try:
        response = httpx.get(settings.base_url.rstrip('/') + '/models',
                            headers={'Authorization': 'Bearer ' + settings.api_key}, timeout=25)
        payload = response.json()
        selected = [x for x in payload.get('data', []) if 'luna' in str(x.get('id','')).lower()]
        record = {'source': settings.base_url.rstrip('/') + '/models', 'status': response.status_code,
                  'models': selected, 'note': 'Model metadata; do not infer price from missing fields.'}
    except Exception as exc:
        record = {'status':'unavailable','error_type':type(exc).__name__}
    (out/'provider-model-metadata.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(record,ensure_ascii=False))
